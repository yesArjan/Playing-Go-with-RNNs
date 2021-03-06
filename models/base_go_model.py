import tensorflow as tf

import copy
import functools


class GoModel(object):
    """Base Go Model."""
    def __init__(self,
                 hparams,
                 mode=tf.estimator.ModeKeys.TRAIN):
        """Save hparams and set model to mode.

        Args:
            hparams: (tf.contrib.training.HParams) contains hyperparameters of the model
            mode: (tf.estimator.ModeKeys) TRAIN, EVAL or PREDICT
        """
        hparams = copy.copy(hparams)
        self._original_hparams = hparams
        self.set_mode(mode)

        self.my_conv2d = functools.partial(
            tf.layers.conv2d,
            strides=1,
            padding="SAME",
            data_format="channels_first",
            use_bias=False)

        self.my_batchnorm = functools.partial(
            tf.layers.batch_normalization,
            axis=1,
            momentum=.95,
            epsilon=1e-5,
            center=True,
            scale=True,
            fused=True)

    @property
    def hparams(self):
        return self._hparams

    def set_mode(self, mode):
        """Set hp with the given mode."""
        tf.logging.info("Setting GoModel mode to '%s'", mode)
        hparams = copy.copy(self._original_hparams)
        hparams.add_hparam("mode", mode)
        # When not in training mode, set all forms of dropout to zero.
        if mode != tf.estimator.ModeKeys.TRAIN:
            for key in hparams.values():
                if key.endswith("dropout") or key == "label_smoothing":
                    tf.logging.info("Setting hp.%s to 0.0", key)
                    setattr(hparams, key, 0.0)
        self._hparams = hparams

    def conv_block_in(self, inputs):
        """Conv Block as in the AlphaGo Zero paper.

        Args:
            inputs: (tf.Tensor) input of the conv block
        Returns:
            (tf.Tensor) output of the conv block
        """
        hp = self._hparams

        is_training = hp.mode == tf.estimator.ModeKeys.TRAIN
        filters = hp.num_filters

        conv_output = self.my_conv2d(inputs, filters=filters, kernel_size=3)
        conv_output = self.my_batchnorm(conv_output, training=is_training)
        conv_output = tf.nn.relu(conv_output)

        return conv_output

    def residual_block(self, inputs):
        """Residual Block as in the AlphaGo Zero paper.

        Args:
            inputs: (tf.Tensor) input of the residual block
        Returns:
            (tf.Tensor) output of the residual block
        """
        hp = self._hparams

        is_training = hp.mode == tf.estimator.ModeKeys.TRAIN
        filters = hp.num_filters

        add = tf.add

        conv1 = self.my_conv2d(inputs, filters=filters, kernel_size=3)
        conv1 = self.my_batchnorm(conv1, training=is_training)
        conv1 = tf.nn.relu(conv1)

        conv2 = self.my_conv2d(conv1, filters=filters, kernel_size=3)
        conv2 = self.my_batchnorm(conv2, training=is_training)

        add_residual = add(conv2, inputs)
        output = tf.nn.relu(add_residual)

        return output

    def bottom(self, features):
        """Transform features.

        Args:
            features: (dict) contains the inputs of the graph (features, labels...)
                this can be `tf.placeholder` or outputs of `tf.data`
        Returns:
            (dict) contains transformed inputs of the graph (features, labels...)
        """
        self.max_game_length = tf.reduce_max(features["game_length"])
        return features

    def body(self, features):
        """Define graph operations for the output that will be passed into the policy and value heads.

        Args:
            features: (dict) contains the inputs of the graph (features, labels...)
                this can be `tf.placeholder` or outputs of `tf.data`
        Returns:
            (tf.Tensor) output of the model before the policy and value heads
        """
        raise NotImplementedError("Abstract Method")

    def top(self, body_output, features):
        """Define graph operations for the policy and value heads.

        Args:
            body_output: (tf.Tensor) output of the body
            features: (dict) contains the inputs of the graph (features, labels...)
                this can be `tf.placeholder` or outputs of `tf.data`
        Returns:
            (tf.Tensor) output of the policy head
            (tf.Tensor) output of the value head
        """
        raise NotImplementedError("Abstract Method")

    def loss(self, logits, features):
        """Define graph operations of the loss as in the AlphaGo Zero paper.

        Args:
            logits: ([tf.Tensor, tf.Tensor]) outputs of the policy and value heads
            features: (dict) contains the inputs of the graph (features, labels...)
                this can be `tf.placeholder` or outputs of `tf.data`
        Returns:
            ([tf.Tensor, tf.Tensor, tf.Tensor]) policy loss, value loss and l2 regularization loss
            ([tf.Tensor, tf.Tensor]) policy losses and value losses before mean of the batch
        """
        raise NotImplementedError("Abstract Method")

    def policy_accuracy(self, features, predictions, mask=None):
        """Define graph operations for the policy accuracy.

        Args:
            features: (dict) contains the inputs of the graph (features, labels...)
                this can be `tf.placeholder` or outputs of `tf.data`
            predictions: (tf.Tensor) argmax of the policy output
            mask: (tf.Tensor) with dtype tf.bool as mask for the predictions
        Returns:
            (tf.Tensor) output of the model before the policy and value heads
        """
        raise NotImplementedError("Abstract Method")

    def model_fn(self, features, mode):
        """Model function defining the graph operations.

        Args:
            mode: (tf.estimator.ModeKeys) TRAIN, EVAL or PREDICT
            features: (dict) contains the inputs of the graph (features, labels...)
                this can be `tf.placeholder` or outputs of `tf.data`
        Returns:
            model_spec: (dict) contains the graph operations or nodes needed for training / evaluation

        source https://github.com/cs230-stanford/cs230-code-examples/blob/master/tensorflow/nlp/model/model_fn.py
        """
        self.set_mode(mode)
        hp = self._hparams

        reuse = mode == tf.estimator.ModeKeys.EVAL
        is_training = mode == tf.estimator.ModeKeys.TRAIN

        with tf.variable_scope('model', reuse=reuse):
            transformed_features = self.bottom(features)
            body_out = self.body(transformed_features)
            top_out = self.top(body_out, transformed_features)
            p_logits, v_out = top_out

            with tf.variable_scope('predictions'):
                p_preds = tf.nn.softmax(p_logits, name='policy_predictions')
                p_preds_idx = tf.argmax(p_preds, -1, output_type=tf.int32)
                v_preds = v_out

        if mode == tf.estimator.ModeKeys.PREDICT:
            model_spec = transformed_features
            variable_init_op = tf.group(*[tf.global_variables_initializer(), tf.tables_initializer()])
            model_spec['variable_init_op'] = variable_init_op

            model_spec['policy_predictions'] = p_preds
            model_spec['value_predictions'] = v_preds

            return model_spec

        with tf.variable_scope('combined_loss'):
            loss_list, losses_list = self.loss(top_out, transformed_features)

            p_loss, v_loss, l2_loss = loss_list

            loss = hp.value_loss_weight * v_loss + p_loss + hp.reg_strength * l2_loss

        p_targets = features["p_targets"]
        p_acc = self.policy_accuracy(transformed_features, p_preds_idx)

        if is_training:
            with tf.variable_scope('train_ops'):
                global_step = tf.train.get_or_create_global_step()
                learning_rate = tf.train.piecewise_constant(global_step, hp.lr_boundaries, hp.lr_rates)

                optimizer = tf.train.MomentumOptimizer(learning_rate, hp.sgd_momentum)

                with tf.control_dependencies(tf.get_collection(tf.GraphKeys.UPDATE_OPS)):
                    train_op = optimizer.minimize(loss, global_step=global_step)

        # -----------------------------------------------------------
        # METRICS AND SUMMARIES
        # Metrics for evaluation using tf.metrics (average over whole dataset)
        with tf.variable_scope("metrics"):
            metrics = {
                'policy_accuracy': tf.metrics.accuracy(labels=p_targets, predictions=p_preds_idx),
                'loss': tf.metrics.mean(loss),
                'policy_loss': tf.metrics.mean(p_loss),
                'value_loss': tf.metrics.mean(v_loss),
            }
            if hp.use_gogod_data and hp.use_kgs_data:
                dataset = features["dataset_name"]
                mask_gogod = tf.equal(dataset, "gogod")
                mask_kgs = tf.equal(dataset, "kgs")

                p_losses, v_losses = losses_list

                metrics.update({
                    'policy_accuracy_gogod': tf.metrics.accuracy(labels=p_targets, predictions=p_preds_idx,
                                                                 weights=mask_gogod),
                    'policy_accuracy_kgs': tf.metrics.accuracy(labels=p_targets, predictions=p_preds_idx,
                                                               weights=mask_kgs),
                    'policy_loss_gogod': tf.metrics.mean(p_losses, weights=mask_gogod),
                    'policy_loss_kgs': tf.metrics.mean(p_losses, weights=mask_kgs),
                    'value_loss_gogod': tf.metrics.mean(v_losses, weights=mask_gogod),
                    'value_loss_kgs': tf.metrics.mean(v_losses, weights=mask_kgs),
                })

        # Group the update ops for the tf.metrics
        update_metrics_op = tf.group(*[op for _, op in metrics.values()])

        # Get the op to reset the local variables used in tf.metrics
        metric_variables = tf.get_collection(tf.GraphKeys.LOCAL_VARIABLES, scope="metrics")
        metrics_init_op = tf.variables_initializer(metric_variables)

        # Summaries for training
        tf.summary.scalar('policy_accuracy', p_acc)
        tf.summary.scalar('loss', loss)
        tf.summary.scalar('policy_loss', p_loss)
        tf.summary.scalar('value_loss', v_loss)

        if hp.use_gogod_data and hp.use_kgs_data:
            p_acc_gogod = self.policy_accuracy(transformed_features, p_preds_idx, mask_gogod)
            tf.summary.scalar('policy_accuracy_gogod', p_acc_gogod)

            p_acc_kgs = self.policy_accuracy(transformed_features, p_preds_idx, mask_kgs)
            tf.summary.scalar('policy_accuracy_kgs', p_acc_kgs)

            p_losses_gogod = tf.boolean_mask(p_losses, mask_gogod)
            p_loss_gogod = tf.reduce_mean(p_losses_gogod)
            tf.summary.scalar('policy_loss_gogod', p_loss_gogod)

            p_losses_kgs = tf.boolean_mask(p_losses, mask_kgs)
            p_loss_kgs = tf.reduce_mean(p_losses_kgs)
            tf.summary.scalar('policy_loss_kgs', p_loss_kgs)

            v_losses_gogod = tf.boolean_mask(v_losses, mask_gogod)
            v_loss_gogod = tf.reduce_mean(v_losses_gogod)
            tf.summary.scalar('value_loss_gogod', v_loss_gogod)

            v_losses_kgs = tf.boolean_mask(v_losses, mask_kgs)
            v_loss_kgs = tf.reduce_mean(v_losses_kgs)
            tf.summary.scalar('value_loss_kgs', v_loss_kgs)

        # -----------------------------------------------------------
        # MODEL SPECIFICATION
        # Create the model specification and return it
        # It contains nodes or operations in the graph that will be used for training and evaluation
        model_spec = transformed_features
        variable_init_op = tf.group(*[tf.global_variables_initializer(), tf.tables_initializer()])
        model_spec['variable_init_op'] = variable_init_op

        model_spec['policy_predictions'] = p_preds
        model_spec['value_predictions'] = v_preds

        model_spec['value_loss'] = v_loss
        model_spec['loss'] = loss

        model_spec['policy_accuracy'] = p_acc

        model_spec['metrics_init_op'] = metrics_init_op
        model_spec['metrics'] = metrics
        model_spec['update_metrics'] = update_metrics_op
        model_spec['summary_op'] = tf.summary.merge_all()

        if is_training:
            model_spec['train_op'] = train_op

        return model_spec
