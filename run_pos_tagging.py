# coding=utf-8
# Copyright 2018 The Google AI Language Team Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""BERT finetuning runner."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import csv
import json
import os
import tensorflow as tf
import functools

import modeling
import optimization
import tokenization


flags = tf.flags

FLAGS = flags.FLAGS

LOCAL_MODEL_DIR = "/tmp/model_dir"
DUMMY_TAG = "DUMMY"

## Required parameters
flags.DEFINE_string(
    "data_dir", None,
    "The input data dir. Should contain the pos tagging files "
    "(or other data files) for the task.")

flags.DEFINE_string(
    "bert_config_file", None,
    "The config json file corresponding to the pre-trained BERT model. "
    "This specifies the model architecture.")

flags.DEFINE_string("vocab_file", None,
                    "The vocabulary file that the BERT model was trained on.")

flags.DEFINE_string(
    "output_dir", None,
    "The output directory where the model checkpoints will be written.")

## Other parameters

flags.DEFINE_string(
    "init_checkpoint", None,
    "Initial checkpoint (usually from a pre-trained BERT model).")

flags.DEFINE_bool(
    "do_lower_case", True,
    "Whether to lower case the input text. Should be True for uncased "
    "models and False for cased models.")

flags.DEFINE_integer(
    "max_seq_length", 128,
    "The maximum total input sequence length after WordPiece tokenization. "
    "Sequences longer than this will be truncated, and sequences shorter "
    "than this will be padded.")

flags.DEFINE_bool("do_train", False, "Whether to run training.")

flags.DEFINE_bool("do_eval", False, "Whether to run eval on the dev set.")

flags.DEFINE_bool(
    "do_predict", False,
    "Whether to run the model in inference mode on the test set.")

flags.DEFINE_integer("train_batch_size", 32, "Total batch size for training.")

flags.DEFINE_integer("eval_batch_size", 8, "Total batch size for eval.")

flags.DEFINE_integer("predict_batch_size", 8, "Total batch size for predict.")

flags.DEFINE_float("learning_rate", 5e-5, "The initial learning rate for Adam.")

flags.DEFINE_float("num_train_epochs", 3.0,
                   "Total number of training epochs to perform.")

flags.DEFINE_float(
    "warmup_proportion", 0.1,
    "Proportion of training to perform linear learning rate warmup for. "
    "E.g., 0.1 = 10% of training.")

flags.DEFINE_integer("save_checkpoints_steps", 1000,
                     "How often to save the model checkpoint.")

flags.DEFINE_integer("iterations_per_loop", 1000,
                     "How many steps to make in each estimator call.")


class InputExample(object):
    """A single training/test example for simple pos tagging prediction."""

    def __init__(self, guid, text, tags=None):
        """Constructs a InputExample.

        Args:
        guid: Unique id for the example.
        text: string. The untokenized text of the first sequence. For single
            sequence tasks, only this sequence must be specified.
        tags: (Optional) string. The tag labels of the example. This should be
            specified for train and dev examples, but not for test examples.
        """
        self.guid = guid
        self.text = text
        self.tags = tags


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self,
                input_ids,
                input_mask,
                segment_ids,
                tag_ids,
                 sentence_len):
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.tag_ids = tag_ids
        self.sentence_len = sentence_len


class PosProcessor():
    """Processor for the POS tag set."""

    def __init__(self, data_dir):
        self.language = "zh"
        self.data_dir = data_dir

    def get_train_examples(self):
        """See base class."""
        lines = self._read_data_file(self.data_dir + "POS_small.train")
        examples = []
        for (i, line) in enumerate(lines):
            guid = "train-%d" % (i)
            line = line.split("\t")
            text = tokenization.convert_to_unicode(line[0])
            tags = tokenization.convert_to_unicode(line[1])
            tags = tags.split(" ")
            examples.append(
                InputExample(guid=guid, text=text, tags=tags))
        return examples

    def get_dev_examples(self):
        """See base class."""
        lines = self._read_data_file(self.data_dir + "POS_small.dev")
        examples = []
        for (i, line) in enumerate(lines):
            guid = "dev-%d" % (i)
            line = line.split("/t")
            text = tokenization.convert_to_unicode(line[0])
            tags = tokenization.convert_to_unicode(line[1])
            tags = tags.split(" ")
            examples.append(
                InputExample(guid=guid, text=text, tags=tags))
        return examples

    def get_test_examples(self):
        lines = self._read_data_file(self.data_dir + "POS_small.test")
        examples = []
        for (i, line) in enumerate(lines):
            guid = "test-%d" % (i)
            line = line.split("/t")
            if len(line) == 2:
                text = tokenization.convert_to_unicode(line[0])
                tags = tokenization.convert_to_unicode(line[1])
                tags = tags.split(" ")
            else:
                text = tokenization.convert_to_unicode(line[0].strip())
                tags = [DUMMY_TAG for _ in range(len(line[0].split()))]
            examples.append(
                InputExample(guid=guid, text=text, tags=tags))
        return examples

    @classmethod
    def _read_data_file(cls, input_file):
        """Reads a tab separated value file."""
        with tf.gfile.Open(input_file, "r") as f:
            lines = []
            for line in f:
                lines.append(line.strip())
            return lines

    def get_labels(self):

        with open(self.data_dir + "tags.json") as f_in:
            tags = json.load(f_in)
        tag_id_map = {tags[i]: i + 1 for i in range(len(tags))}
        # Manually add "POS" into tag_id_map
        tag_id_map["PAD"] = 0
        return tag_id_map



def convert_single_example(ex_index, example, tag_id_map, max_seq_length,
                           tokenizer):
    """Converts a single `InputExample` into a single `InputFeatures`."""

    tags = example.tags
    tokens = tokenizer.tokenize(example.text)
    # Account for [CLS] and [SEP] with "- 2"
    if len(tokens) > max_seq_length - 2:
        tokens = tokens[0: (max_seq_length - 2)]
        tags = tags[0: (max_seq_length - 2)]

    # Record real sentence of sentence
    sentence_len = len(tokens)

    # The convention in BERT is:
    # (a) For sequence pairs:
    #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
    #  type_ids: 0     0  0    0    0     0       0 0     1  1  1  1   1 1
    # (b) For single sequences:
    #  tokens:   [CLS] the dog is hairy . [SEP]
    #  type_ids: 0     0   0   0  0     0 0
    #
    # Where "type_ids" are used to indicate whether this is the first
    # sequence or the second sequence. The embedding vectors for `type=0` and
    # `type=1` were learned during pre-training and are added to the wordpiece
    # embedding vector (and position vector). This is not *strictly* necessary
    # since the [SEP] token unambiguously separates the sequences, but it makes
    # it easier for the model to learn the concept of sequences.
    #
    # For classification tasks, the first vector (corresponding to [CLS]) is
    # used as the "sentence vector". Note that this only makes sense because
    # the entire model is fine-tuned.
    tokens = ["[CLS]"] + tokens + ["[SEP]"]
    tags.append("PAD")
    segment_ids = [0] * len(tokens)

    input_ids = tokenizer.convert_tokens_to_ids(tokens)

    # The mask has 1 for real tokens and 0 for padding tokens. Only real
    # tokens are attended to.
    input_mask = [1] * len(input_ids)

    # Zero-pad up to the sequence length.
    while len(input_ids) < max_seq_length:
        input_ids.append(0)
        input_mask.append(0)
        segment_ids.append(0)
        tags.append("PAD")

    assert len(input_ids) == max_seq_length
    assert len(input_mask) == max_seq_length
    assert len(segment_ids) == max_seq_length

    tags_id = []
    for tag in tags:
        tags_id.append(tag_id_map[tag])
    if ex_index < 5:
        tf.logging.info("*** Example ***")
        tf.logging.info("guid: %s" % (example.guid))
        tf.logging.info("tokens: %s" % " ".join(
            [tokenization.printable_text(x) for x in tokens]))
        tf.logging.info("tags: %s" % " ".join(
            [tokenization.printable_text(x) for x in tags[: sentence_len]]))
        tf.logging.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
        tf.logging.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
        tf.logging.info("segment_ids: %s" % " ".join([str(x) for x in segment_ids]))

    feature = InputFeatures(
        input_ids=input_ids,
        input_mask=input_mask,
        segment_ids=segment_ids,
        tag_ids=tags_id,
        sentence_len=sentence_len)
    return feature


def file_based_convert_examples_to_features(
    examples, tag_id_map, max_seq_length, tokenizer, output_file):
    """Convert a set of `InputExample`s to a TFRecord file."""

    writer = tf.python_io.TFRecordWriter(output_file)

    for (ex_index, example) in enumerate(examples):
        if ex_index % 10000 == 0:
            tf.logging.info("Writing example %d of %d"
                            % (ex_index, len(examples)))

        feature = convert_single_example(ex_index, example, tag_id_map,
                                        max_seq_length, tokenizer)

        def create_int_feature(values):
            f = tf.train.Feature(int64_list=tf.train.Int64List(value=list(values)))
            return f

        features = collections.OrderedDict()
        features["input_ids"] = create_int_feature(feature.input_ids)
        features["input_mask"] = create_int_feature(feature.input_mask)
        features["segment_ids"] = create_int_feature(feature.segment_ids)
        features["tag_ids"] = create_int_feature(feature.tag_ids)
        features["sentence_len"] = create_int_feature([feature.sentence_len])

        tf_example = tf.train.Example(features=tf.train.Features(feature=features))
        writer.write(tf_example.SerializeToString())
    writer.close()


def file_based_input_fn_builder(input_file, seq_length, is_training,
                                drop_remainder):
    """Creates an `input_fn` closure to be passed to TPUEstimator."""

    name_to_features = {
        "input_ids": tf.FixedLenFeature([seq_length], tf.int64),
        "input_mask": tf.FixedLenFeature([seq_length], tf.int64),
        "segment_ids": tf.FixedLenFeature([seq_length], tf.int64),
        "sentence_len": tf.FixedLenFeature([], tf.int64),
        "tag_ids": tf.FixedLenFeature([seq_length - 1], tf.int64),
    }

    def _decode_record(record, name_to_features):
        """Decodes a record to a TensorFlow example."""
        example = tf.parse_single_example(record, name_to_features)

        # tf.Example only supports tf.int64, but the TPU only supports tf.int32.
        # So cast all int64 to int32.
        for name in list(example.keys()):
            t = example[name]
            if t.dtype == tf.int64:
                t = tf.to_int32(t)
            example[name] = t

        return example

    def input_fn(params):
        """The actual input function."""
        batch_size = None
        if is_training:
            batch_size = params.train_batch_size
        else:
            batch_size = params.eval_batch_size
        # batch_size = params["batch_size"]

        # For training, we want a lot of parallel reading and shuffling.
        # For eval, we want no shuffling and parallel reading doesn't matter.
        d = tf.data.TFRecordDataset(input_file)
        if is_training:
            d = d.repeat()
            d = d.shuffle(buffer_size=100)

        d = d.apply(
            tf.contrib.data.map_and_batch(
                lambda record: _decode_record(record, name_to_features),
                batch_size=batch_size,
                drop_remainder=drop_remainder))

        return d

    return input_fn


def create_model(bert_config, is_training, input_ids, input_mask, segment_ids,
                 num_tags, osentences_len):
    """Creates a classification model."""
    model = modeling.BertModel(
        config=bert_config,
        is_training=is_training,
        input_ids=input_ids,
        input_mask=input_mask,
        token_type_ids=segment_ids)

    # In the demo, we are doing a simple classification task on the entire
    # segment.
    #
    # If you want to use the token-level output, use model.get_sequence_output()
    # instead.
    # output_layer = model.get_pooled_output()
    output_layer = model.get_sequence_output()

    with tf.variable_scope("loss"):
        if is_training:
            # I.e., 0.1 dropout
            output_layer = tf.nn.dropout(output_layer, keep_prob=0.9)

        _, sentence_len, _ = output_layer.shape.as_list()

        # Ignore the [cls] token in the head of the sentence.
        output_layer = output_layer[:, 1:, :]

        # FC layer
        logits = tf.layers.dense(output_layer, num_tags)

        # crf layer
        crf_params = tf.get_variable(name='crf', shape=[num_tags, num_tags],
                                     dtype=tf.float32)
        pred_id, _ = tf.contrib.crf.crf_decode(logits, crf_params,
                                               osentences_len)
        return logits, crf_params, pred_id, sentence_len


def model_fn_builder(bert_config, init_checkpoint, learning_rate,
                     num_train_steps, num_warmup_steps):
    """Returns `model_fn` closure for TPUEstimator."""

    def model_fn(features, labels, mode, params):  # pylint: disable=unused-argument
        """The `model_fn` for TPUEstimator."""

        tf.logging.info("*** Features ***")
        for name in sorted(features.keys()):
            tf.logging.info("  name = %s, shape = %s" %
                            (name, features[name].shape))
        tag_to_id, id_to_tag, num_tags = get_tag_map_tensors(params)

        input_ids = features["input_ids"]
        input_mask = features["input_mask"]
        segment_ids = features["segment_ids"]
        tag_ids = features["tag_ids"]
        osentences_len = features["sentence_len"]

        is_training = (mode == tf.estimator.ModeKeys.TRAIN)

        (logits, crf_params, pred_ids, sentence_len) = create_model(
            bert_config, is_training, input_ids, input_mask, segment_ids,
            num_tags, osentences_len)

        if mode == tf.estimator.ModeKeys.PREDICT:
            pred_tags = id_to_tag.lookup(tf.to_int64(pred_ids))
            predictions = {
                "pred_ids": pred_ids,
                "pred_string": pred_tags
            }
            output_spec = tf.estimator.EstimatorSpec(
                mode=mode,
                predictions=predictions, )
            return output_spec

        tvars = tf.trainable_variables()
        initialized_variable_names = {}
        if init_checkpoint:
            (assignment_map, initialized_variable_names) = \
                modeling.get_assignment_map_from_checkpoint(tvars,
                                                            init_checkpoint)
            tf.train.init_from_checkpoint(init_checkpoint, assignment_map)

        tf.logging.info("**** Trainable Variables ****")
        for var in tvars:
            init_string = ""
            if var.name in initialized_variable_names:
                init_string = ", *INIT_FROM_CKPT*"
            tf.logging.info("  name = %s, shape = %s%s", var.name, var.shape,
                            init_string)

        # Calculate the loss prediction
        log_likehood, _ = tf.contrib.crf.crf_log_likelihood(logits, tag_ids,
                                                         osentences_len,
                                                         crf_params)
        loss = tf.reduce_mean(-log_likehood)

        # metric
        weights = tf.sequence_mask(osentences_len, sentence_len - 1)
        metrics = {
            'acc': tf.metrics.accuracy(tag_ids, pred_ids, weights),
            'loss': loss,
        }

        # write summary
        for metric_name, op in metrics.items():
            if metric_name == 'loss':
                tf.summary.scalar(metric_name, op)
            else:
                tf.summary.scalar(metric_name, op[1])
        output_spec = None
        if mode == tf.estimator.ModeKeys.TRAIN:
            train_op = optimization.create_optimizer(
                loss, learning_rate, num_train_steps,
                num_warmup_steps, use_tpu=False)

            output_spec = tf.estimator.EstimatorSpec(
                mode=mode,
                train_op=train_op,
                loss=loss)
        elif mode == tf.estimator.ModeKeys.EVAL:

            output_spec = tf.estimator.EstimatorSpec(
                mode=mode,
                loss=loss,
                eval_metric_ops=metrics)
        return output_spec

    return model_fn


def get_tag_map_tensors(params):
    with open(params.data_dir + "tags.json") as f_in:
        tags = json.load(f_in)
    tag_to_id = {tags[i]: i for i in range(len(tags))}
    tag_to_id["PAD"] = 0

    id_to_tag = {v: k for k, v in tag_to_id.items()}
    num_tags = len(tags)
    return tag_to_id, id_to_tag, num_tags


# This function is not used by this file but is still used by the Colab and
# people who depend on it.
def input_fn_builder(features, seq_length, is_training, drop_remainder):
    """Creates an `input_fn` closure to be passed to TPUEstimator."""

    all_input_ids = []
    all_input_mask = []
    all_segment_ids = []
    all_label_ids = []

    for feature in features:
        all_input_ids.append(feature.input_ids)
        all_input_mask.append(feature.input_mask)
        all_segment_ids.append(feature.segment_ids)
        all_label_ids.append(feature.label_id)

    def input_fn(params):
        """The actual input function."""

        # batch_size = params["batch_size"]
        batch_size = None
        if is_training:
            batch_size = params.train_batch_size
        else:
            batch_size = params.eval_batch_size

        num_examples = len(features)

        # This is for demo purposes and does NOT scale to large data sets. We do
        # not use Dataset.from_generator() because that uses tf.py_func which is
        # not TPU compatible. The right way to load data is with TFRecordReader.
        d = tf.data.Dataset.from_tensor_slices({
            "input_ids":
                tf.constant(
                    all_input_ids, shape=[num_examples, seq_length],
                    dtype=tf.int32),
            "input_mask":
                tf.constant(
                    all_input_mask,
                    shape=[num_examples, seq_length],
                    dtype=tf.int32),
            "segment_ids":
                tf.constant(
                    all_segment_ids,
                    shape=[num_examples, seq_length],
                    dtype=tf.int32),
            "label_ids":
                tf.constant(all_label_ids, shape=[num_examples], dtype=tf.int32),
        })

        if is_training:
            d = d.repeat()
            d = d.shuffle(buffer_size=100)

        d = d.batch(batch_size=batch_size, drop_remainder=drop_remainder)
        return d

    return input_fn


# This function is not used by this file but is still used by the Colab and
# people who depend on it.
def convert_examples_to_features(examples, label_list, max_seq_length,
                                 tokenizer):
    """Convert a set of `InputExample`s to a list of `InputFeatures`."""

    features = []
    for (ex_index, example) in enumerate(examples):
        if ex_index % 10000 == 0:
            tf.logging.info("Writing example %d of %d" %
                            (ex_index, len(examples)))

        feature = convert_single_example(ex_index, example, label_list,
                                        max_seq_length, tokenizer)

        features.append(feature)
    return features


def main(_):
    tf.logging.set_verbosity(tf.logging.INFO)


    tokenization.validate_case_matches_checkpoint(FLAGS.do_lower_case,
                                                FLAGS.init_checkpoint)

    if not FLAGS.do_train and not FLAGS.do_eval and not FLAGS.do_predict:
        raise ValueError(
            "At least one of `do_train`, `do_eval` or `do_predict' must be True.")

    bert_config = modeling.BertConfig.from_json_file(FLAGS.bert_config_file)

    if FLAGS.max_seq_length > bert_config.max_position_embeddings:
        raise ValueError(
            "Cannot use sequence length %d because the BERT model "
            "was only trained up to sequence length %d" %
            (FLAGS.max_seq_length, bert_config.max_position_embeddings))

    tf.gfile.MakeDirs(FLAGS.output_dir)

    processor = PosProcessor(FLAGS.data_dir)
    label_id_map = processor.get_labels()

    tokenizer = tokenization.BasicTokenizer(vocab_file=FLAGS.vocab_file,
                                            do_lower_case=FLAGS.do_lower_case)

    train_examples = None
    num_train_steps = None
    num_warmup_steps = None
    if FLAGS.do_train:
        train_examples = processor.get_train_examples()
        num_train_steps = int(
            len(train_examples)
            / FLAGS.train_batch_size * FLAGS.num_train_epochs)
        num_warmup_steps = int(num_train_steps * FLAGS.warmup_proportion)

    model_fn = model_fn_builder(
        bert_config=bert_config,
        init_checkpoint=FLAGS.init_checkpoint,
        learning_rate=FLAGS.learning_rate,
        num_train_steps=num_train_steps,
        num_warmup_steps=num_warmup_steps)
    model_fn = functools.partial(model_fn, params=FLAGS)

    # Original config
    config = tf.estimator.RunConfig(save_checkpoints_steps=1000)

    estimator = tf.estimator.Estimator(
        model_fn=model_fn,
        config=config,
        model_dir=LOCAL_MODEL_DIR)

    if FLAGS.do_train:
        train_file = os.path.join(FLAGS.output_dir, "train.tf_record")
        file_based_convert_examples_to_features(
            train_examples, label_id_map, FLAGS.max_seq_length, tokenizer, train_file)
        tf.logging.info("***** Running training *****")
        tf.logging.info("  Num examples = %d", len(train_examples))
        tf.logging.info("  Batch size = %d", FLAGS.train_batch_size)
        tf.logging.info("  Num steps = %d", num_train_steps)
        train_input_fn = file_based_input_fn_builder(
            input_file=train_file,
            seq_length=FLAGS.max_seq_length,
            is_training=True,
            drop_remainder=True)
        train_input_fn = functools.partial(train_input_fn, params=FLAGS)
        estimator.train(input_fn=train_input_fn, max_steps=num_train_steps)

    if FLAGS.do_eval:
        eval_examples = processor.get_dev_examples()
        num_actual_eval_examples = len(eval_examples)

        eval_file = os.path.join(FLAGS.output_dir, "eval.tf_record")
        file_based_convert_examples_to_features(
            eval_examples, label_id_map, FLAGS.max_seq_length, tokenizer, eval_file)

        tf.logging.info("***** Running evaluation *****")
        tf.logging.info("  Num examples = %d (%d actual, %d padding)",
                        len(eval_examples), num_actual_eval_examples,
                        len(eval_examples) - num_actual_eval_examples)
        tf.logging.info("  Batch size = %d", FLAGS.eval_batch_size)

        # This tells the estimator to run through the entire set.
        eval_steps = int(len(eval_examples) // FLAGS.eval_batch_size)

        eval_input_fn = file_based_input_fn_builder(
            input_file=eval_file,
            seq_length=FLAGS.max_seq_length,
            is_training=False,
            drop_remainder=False)
        eval_input_fn = functools.partial(eval_input_fn, params=FLAGS)

        result = estimator.evaluate(input_fn=eval_input_fn, steps=eval_steps)

        output_eval_file = os.path.join(FLAGS.output_dir, "eval_results.txt")
        with tf.gfile.GFile(output_eval_file, "w") as writer:
            tf.logging.info("***** Eval results *****")
            for key in sorted(result.keys()):
                tf.logging.info("  %s = %s", key, str(result[key]))
                writer.write("%s = %s\n" % (key, str(result[key])))

    if FLAGS.do_predict:
        predict_examples = processor.get_test_examples()
        num_actual_predict_examples = len(predict_examples)

        predict_file = os.path.join(FLAGS.output_dir, "predict.tf_record")
        file_based_convert_examples_to_features(predict_examples, label_id_map,
                                                FLAGS.max_seq_length, tokenizer,
                                                predict_file)

        tf.logging.info("***** Running prediction*****")
        tf.logging.info("  Num examples = %d (%d actual, %d padding)",
                        len(predict_examples), num_actual_predict_examples,
                        len(predict_examples) - num_actual_predict_examples)
        tf.logging.info("  Batch size = %d", FLAGS.predict_batch_size)

        predict_input_fn = file_based_input_fn_builder(
            input_file=predict_file,
            seq_length=FLAGS.max_seq_length,
            is_training=False,
            drop_remainder=False)

        predict_input_fn = functools.partial(predict_input_fn, params=FLAGS)
        result = estimator.predict(input_fn=predict_input_fn)

        output_predict_file = os.path.join(FLAGS.output_dir, "test_results.tsv")
        with tf.gfile.GFile(output_predict_file, "w") as writer:
            num_written_lines = 0
            tf.logging.info("***** Predict results *****")
            for (i, prediction) in enumerate(result):
                probabilities = prediction["probabilities"]
                if i >= num_actual_predict_examples:
                    break
                output_line = "\t".join(
                    str(class_probability)
                    for class_probability in probabilities) + "\n"
                writer.write(output_line)
                num_written_lines += 1
        assert num_written_lines == num_actual_predict_examples


if __name__ == "__main__":
    flags.mark_flag_as_required("data_dir")
    flags.mark_flag_as_required("vocab_file")
    flags.mark_flag_as_required("bert_config_file")
    flags.mark_flag_as_required("output_dir")
    tf.app.run()
