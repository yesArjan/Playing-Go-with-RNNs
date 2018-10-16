import tensorflow as tf
import random

from data_generators import base_go_problem, go_preprocessing


class GoProblem19(base_go_problem.GoProblem):
    """Go Problem for 19x19 go games."""
    @property
    def board_size(self):
        return 19

    def dataset_filename(self):
        return "go_problem_19"

    @property
    def train_shards(self):
        return 8

    @property
    def is_small(self):
        return False

    def generate_dataset(self, tmp_dir, unzip=True):
        # set random seed to make sure shuffle is recreatable
        random.seed(230)

        data = {
            "train": [],
            "dev": [],
            "test": []
        }

        if self.use_gogod_data:
            data_gogod = self.get_gogod_dataset(tmp_dir, unzip)
            for k in data:
                data[k] += data_gogod[k]

        if self.board_size == 19:
            data_kgs = self.get_kgs_dataset(tmp_dir, unzip)
            for k in data:
                data[k] += data_kgs[k]
        return data


class GoProblem19Rnn(GoProblem19):
    @property
    def is_recurrent(self):
        return True

    def preprocess_example(self, example, mode, hparams):
        example = go_preprocessing.format_example(example, self.board_size)

        example["inputs"].set_shape([None, 3, self.board_size, self.board_size])
        example["legal_moves"].set_shape([None, self.num_moves])
        example["p_targets"].set_shape([None])
        example["v_targets"].set_shape([None])

        example["inputs"] = tf.cast(example["inputs"], tf.float32)
        example["legal_moves"] = tf.cast(example["legal_moves"], tf.float32)
        example["v_targets"] = tf.cast(example["v_targets"], tf.float32)

        if self.sort_sequence_by_color:
            examples = go_preprocessing.split_exmaple_by_color(example)
            example.pop("to_play")

            if mode == tf.estimator.ModeKeys.TRAIN:
                examples[0] = go_preprocessing.random_augmentation(examples[0], self.board_size)

            dataset = tf.data.Dataset.from_tensors(examples[0])

            for ex in examples[1:]:
                if mode == tf.estimator.ModeKeys.TRAIN:
                    ex = go_preprocessing.random_augmentation(ex, self.board_size)
                dat = tf.data.Dataset.from_tensors(ex)
                dataset = dataset.concatenate(dat)
            return dataset
        else:
            example.pop("to_play")
            if mode == tf.estimator.ModeKeys.TRAIN:
                example = go_preprocessing.random_augmentation(example, self.board_size)
            return example


class GoProblem19Cnn(GoProblem19):
    @property
    def is_recurrent(self):
        return False

    def preprocess_example(self, example, mode, hparams):
        example = go_preprocessing.format_example(example, self.board_size)
        example.pop("to_play")

        example["inputs"] = tf.cast(example["inputs"], tf.float32)
        example["legal_moves"] = tf.cast(example["legal_moves"], tf.float32)
        example["v_targets"] = tf.cast(example["v_targets"], tf.float32)

        dataset = go_preprocessing.build_dataset_cnn(example)

        if mode == tf.estimator.ModeKeys.TRAIN:
            def _augment(ex):
                ex = go_preprocessing.random_augmentation(ex, self.board_size, "cnn")
                return ex
            dataset = dataset.map(_augment)

        return dataset
