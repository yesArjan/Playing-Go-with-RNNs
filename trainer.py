# sources:
# https://github.com/cs230-stanford/cs230-code-examples/tree/master/tensorflow/nlp/model/training.py
# https://github.com/cs230-stanford/cs230-code-examples/blob/master/tensorflow/nlp/train.py
#
# https://github.com/cs230-stanford/cs230-code-examples/blob/master/tensorflow/nlp/model/evaluation.py
# https://github.com/cs230-stanford/cs230-code-examples/blob/master/tensorflow/nlp/evaluate.py

import tensorflow as tf
import numpy as np
import os

from utils import utils
from tqdm import trange
from tensor2tensor.data_generators.problem import DatasetSplit


class GoTrainer:
    """Class used to train and evaluate Go Models."""

    def __init__(self, problem, model, hparams, experiment_dir, skip_generate_data=False):
        """Initialize the problem, model and hparams classes."""
        hp = hparams()
        hp.add_hparam("experiment_dir", experiment_dir)

        problem = problem()
        hp = problem.get_hparams(hp)

        if not skip_generate_data:
            tf.logging.info("Creating the datasets...")
            problem.generate_data(hp.data_dir, hp.tmp_dir)
            tf.logging.info("- done")

        model = model(hp)

        self.hp = hp
        self.problem = problem
        self.model = model

    def train_epoch(self, sess, model_spec, num_steps, writer, reset=True):
        """Train the model on `num_steps` batches
        Args:
            sess: (tf.Session) current session
            model_spec: (dict) contains the graph operations or nodes needed for training
            num_steps: (int) train for this number of batches
            writer: (tf.summary.FileWriter) writer for summaries
            reset: (bool) resets the iterator
        """
        hp = self.hp

        # Get relevant graph operations or nodes needed for training
        loss = model_spec['loss']
        train_op = model_spec['train_op']
        update_metrics = model_spec['update_metrics']
        metrics = model_spec['metrics']
        summary_op = model_spec['summary_op']
        global_step = tf.train.get_global_step()

        # Load the training dataset into the pipeline and initialize the metrics local variables
        if reset:
            sess.run(model_spec['iterator_init_op'])

        sess.run(model_spec['metrics_init_op'])

        # Use tqdm for progress bar
        t = trange(num_steps)
        try:
            for i in t:
                # Evaluate summaries for tensorboard only once in a while
                if i % hp.save_summary_steps == 0:
                    # Perform a mini-batch update
                    _, _, loss_val, summaries, global_step_val = sess.run([train_op, update_metrics, loss,
                                                                           summary_op, global_step])
                    # Write summaries for tensorboard
                    writer.add_summary(summaries, global_step_val)
                else:
                    _, _, loss_val = sess.run([train_op, update_metrics, loss])
                # Log the loss in the tqdm progress bar
                t.set_postfix(loss='{:05.3f}'.format(loss_val))
        except tf.errors.OutOfRangeError:
            tf.logging.info("Step {}/{} out of range from iterator".format(i+1, num_steps))
            pass

        metrics_values = {k: v[0] for k, v in metrics.items()}
        metrics_val = sess.run(metrics_values)

        return metrics_val

    def evaluate_epoch(self, sess, model_spec, num_steps, writer=None, reset=True):
        """Train the model on `num_steps` batches.
        Args:
            sess: (tf.Session) current session
            model_spec: (dict) contains the graph operations or nodes needed for training
            num_steps: (int) train for this number of batches
            writer: (tf.summary.FileWriter) writer for summaries. Is None if we don't log anything
            reset: (bool) resets the iterator
        """
        update_metrics = model_spec['update_metrics']
        eval_metrics = model_spec['metrics']
        global_step = tf.train.get_global_step()

        # Load the evaluation dataset into the pipeline and initialize the metrics init op
        if reset:
            sess.run(model_spec['iterator_init_op'])

        sess.run(model_spec['metrics_init_op'])

        # compute metrics over the dataset
        try:
            for i in range(num_steps):
                sess.run(update_metrics)
        except tf.errors.OutOfRangeError:
            tf.logging.info("Step {}/{} out of range from iterator".format(i + 1, num_steps))
            pass

        # Get the values of the metrics
        metrics_values = {k: v[0] for k, v in eval_metrics.items()}
        metrics_val = sess.run(metrics_values)

        # Add summaries manually to writer at global_step_val
        if writer is not None:
            global_step_val = sess.run(global_step)
            for tag, val in metrics_val.items():
                summ = tf.Summary(value=[tf.Summary.Value(tag=tag, simple_value=val)])
                writer.add_summary(summ, global_step_val)

        return metrics_val

    @staticmethod
    def metrics_string(metrics_val):
        loss_string = " ; ".join("{}: {:05.3f}".format(k, v) for k, v in metrics_val.items() if "loss" in k)
        acc_string = " ; ".join("{}: {:05.3f}".format(k, v) for k, v in metrics_val.items() if "acc" in k)
        return loss_string, acc_string

    def train_and_evaluate(self, restore_from=None):
        """Train the model and evaluate every epoch.
        Args:
            restore_from: (string) directory or file containing weights to restore the graph
        """
        hp = self.hp
        experiment_dir = hp.experiment_dir

        tf.logging.info("Starting training for {} epoch(s)".format(hp.num_epochs))

        split = DatasetSplit.TRAIN
        train_model_spec = self._get_model_spec(split)

        split = DatasetSplit.EVAL
        eval_model_spec = self._get_model_spec(split)

        # Initialize tf.Saver instances to save weights during training
        last_saver = tf.train.Saver()  # will keep last 5 epochs
        best_saver = tf.train.Saver(max_to_keep=1)  # only keep 1 best checkpoint (best on eval)
        begin_at_epoch = 0

        with tf.Session() as sess:
            # Initialize model variables
            sess.run(train_model_spec['variable_init_op'])

            # Reload weights from directory if specified
            if restore_from is not None:
                tf.logging.info("Restoring parameters from {}".format(restore_from))
                if os.path.isdir(restore_from):
                    restore_from = tf.train.latest_checkpoint(restore_from)
                    begin_at_epoch = int(restore_from.split('-')[-1])
                last_saver.restore(sess, restore_from)

            # For tensorboard (takes care of writing summaries to files)
            train_writer = tf.summary.FileWriter(os.path.join(experiment_dir, 'train_summaries'), sess.graph)
            eval_writer = tf.summary.FileWriter(os.path.join(experiment_dir, 'eval_summaries'), sess.graph)

            tf.gfile.MakeDirs(os.path.join(experiment_dir, 'last_weights'))
            tf.gfile.MakeDirs(os.path.join(experiment_dir, 'best_weights'))

            best_eval_p_acc = 0.0
            for epoch in range(begin_at_epoch, begin_at_epoch + hp.num_epochs):
                # Run one epoch
                # Compute number of batches in one epoch (one full pass over the training set)
                total_train_steps = (hp.train_size + hp.batch_size - 1) // hp.batch_size
                total_train_steps_list = list(range(total_train_steps))

                length = int(np.ceil(total_train_steps / hp.eval_every))

                split_train_steps = np.array_split(total_train_steps_list, length)
                split_train_steps = [len(l) for l in split_train_steps]

                total_eval_steps = (hp.dev_size + hp.batch_size - 1) // hp.batch_size
                total_eval_steps_list = list(range(total_eval_steps))

                split_eval_steps = np.array_split(total_eval_steps_list, length)
                split_eval_steps = [len(l) for l in split_eval_steps]

                for i, (t_steps, e_steps) in enumerate(zip(split_train_steps, split_eval_steps)):
                    tf.logging.info("Epoch {} - {}/{} with {} train steps and {} eval steps"
                                    .format(epoch + 1, i + 1, len(split_train_steps), t_steps, e_steps))
                    reset = False
                    if i == 0:
                        reset = True

                    train_metrics = self.train_epoch(sess, train_model_spec, t_steps, train_writer, reset)
                    train_loss_string, train_acc_string = self.metrics_string(train_metrics)
                    tf.logging.info("- Train metrics: " + train_acc_string)
                    tf.logging.info("- Train metrics: " + train_loss_string)

                    # Save weights
                    last_save_path = os.path.join(experiment_dir, 'last_weights',
                                                  'after-epoch-{}'.format(epoch + 1))
                    last_saver.save(sess, last_save_path, global_step=i + 1)

                    # Evaluate for one sub epoch on validation set
                    eval_metrics = self.evaluate_epoch(sess, eval_model_spec, e_steps, eval_writer, reset)
                    test_loss_string, test_acc_string = self.metrics_string(eval_metrics)
                    tf.logging.info("- Eval metrics: " + test_acc_string)
                    tf.logging.info("- Eval metrics: " + test_loss_string)

                    # If best_eval, best_save_path
                    eval_p_acc = eval_metrics['policy_accuracy']
                    if eval_p_acc >= best_eval_p_acc:
                        # Store new best accuracy
                        best_eval_p_acc = eval_p_acc
                        # Save weights
                        best_save_path = os.path.join(experiment_dir, 'best_weights',
                                                      'after-epoch-{}'.format(epoch + 1))
                        best_save_path = best_saver.save(sess, best_save_path, global_step=i + 1)
                        tf.logging.info("- Found new best policy accuracy, saving in {}".format(best_save_path))
                        # Save best train metrics in a json file in the model directory
                        best_json_path = os.path.join(experiment_dir, "metrics_train_best_weights.json")
                        utils.save_dict_to_json(train_metrics, best_json_path)
                        # Save best eval metrics in a json file in the model directory
                        best_json_path = os.path.join(experiment_dir, "metrics_eval_best_weights.json")
                        utils.save_dict_to_json(eval_metrics, best_json_path)

                    # Save latest train metrics in a json file in the model directory
                    last_json_path = os.path.join(experiment_dir, "metrics_train_last_weights.json")
                    utils.save_dict_to_json(train_metrics, last_json_path)

                    # Save latest eval metrics in a json file in the model directory
                    last_json_path = os.path.join(experiment_dir, "metrics_eval_last_weights.json")
                    utils.save_dict_to_json(eval_metrics, last_json_path)

    def test(self, restore_from):
        """Test the model
        Args:
            restore_from: (string) directory or file containing weights to restore the graph
        """
        hp = self.hp
        experiment_dir = hp.experiment_dir

        split = DatasetSplit.TEST
        model_spec = self._get_model_spec(split)

        # Initialize tf.Saver
        saver = tf.train.Saver()

        with tf.Session() as sess:
            # Initialize the lookup table
            sess.run(model_spec['variable_init_op'])

            # Reload weights from the weights subdirectory
            save_path = os.path.join(experiment_dir, restore_from)
            if os.path.isdir(save_path):
                save_path = tf.train.latest_checkpoint(save_path)
            saver.restore(sess, save_path)

            # Evaluate
            num_steps = (hp.test_size + hp.batch_size - 1) // hp.batch_size
            metrics = self.evaluate_epoch(sess, model_spec, num_steps)

            loss_string, acc_string = self.metrics_string(metrics)
            tf.logging.info("- Test metrics: " + acc_string)
            tf.logging.info("- Test metrics: " + loss_string)

            metrics_name = '_'.join(restore_from.split('/'))
            save_path = os.path.join(experiment_dir, "metrics_test_{}.json".format(metrics_name))
            utils.save_dict_to_json(metrics, save_path)

    @staticmethod
    def _split_to_mode(split):
        split_to_mode = {
            DatasetSplit.TRAIN: tf.estimator.ModeKeys.TRAIN,
            DatasetSplit.EVAL: tf.estimator.ModeKeys.EVAL,
            DatasetSplit.TEST: tf.estimator.ModeKeys.EVAL
        }
        return split_to_mode[split]

    def _get_model_spec(self, dataset_split):
        """Load dataset and initialize model."""
        problem = self.problem
        hp = self.hp
        mode = self._split_to_mode(dataset_split)

        dataset_kwargs = {
            "dataset_split": dataset_split
        }

        tf.logging.info("Loading the dataset...")
        dataset = problem.input_fn(mode, hp, dataset_kwargs=dataset_kwargs, prevent_repeat=True)
        tf.logging.info("- done.")

        model = self.model

        tf.logging.info("Creating the model...")
        iterator = dataset.make_initializable_iterator()
        init_op = iterator.initializer

        features = iterator.get_next()
        model_spec = model.model_fn(features, mode)
        model_spec["iterator_init_op"] = init_op
        tf.logging.info("- done.")
        return model_spec
