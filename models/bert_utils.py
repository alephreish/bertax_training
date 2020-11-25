import keras
import keras_bert
import tensorflow as tf
from preprocessing.process_inputs import seq2kmers, ALPHABET
from random import randint
import numpy as np
from itertools import product
from logging import info
from misc.metrics import compute_roc, accuracy, loss


# NOTE: uses just keras (instead of tensorflow.keras). Otherwise
# loading of the pre-trained model is likely to fail


def get_token_dict(alph=ALPHABET, k=3):
    """get token dictionary dict generated from `alph` and `k`"""
    token_dict = keras_bert.get_base_dict()
    for word in [''.join(_) for _ in product(alph, repeat=k)]:
        token_dict[word] = len(token_dict)
    return token_dict


def load_bert(bert_path, compile_=False):
    """get bert model from path"""
    custom_objects = {'GlorotNormal': keras.initializers.glorot_normal,
                      'GlorotUniform': keras.initializers.glorot_uniform}
    custom_objects.update(keras_bert.get_custom_objects())
    model = keras.models.load_model(bert_path, compile=compile_,
                                    custom_objects=custom_objects)
    return model


def generate_bert_with_pretrained(pretrained_path, nr_classes=4):
    """get model ready for fine-tuning and the maximum input length"""
    # see https://colab.research.google.com/github/CyberZHG/keras-bert
    # /blob/master/demo/tune/keras_bert_classification_tpu.ipynb
    model = load_bert(pretrained_path)
    inputs = model.inputs[:2]
    nsp_dense_layer = model.get_layer(name='NSP-Dense').output
    softmax_layer = keras.layers.Dense(nr_classes, activation='softmax')(
        nsp_dense_layer)
    model_fine = keras.Model(inputs=inputs, outputs=softmax_layer)
    return model_fine


def generate_bert_with_pretrained_multi_tax(pretrained_path, nr_classes=(4, 30, 100)):
    """get model ready for fine-tuning and the maximum input length"""
    # see https://colab.research.google.com/github/CyberZHG/keras-bert
    # /blob/master/demo/tune/keras_bert_classification_tpu.ipynb
    custom_objects = {'GlorotNormal': keras.initializers.glorot_normal,
                      'GlorotUniform': keras.initializers.glorot_uniform}
    custom_objects.update(keras_bert.get_custom_objects())
    model = keras.models.load_model(pretrained_path, compile=False,
                                    custom_objects=custom_objects)
    inputs = model.inputs[:2]
    nsp_dense_layer = model.get_layer(name='NSP-Dense').output

    superkingdoms, families, species = nr_classes
    superkingdoms_out = keras.layers.Dense(superkingdoms, activation='softmax',name="superkingdoms_softmax")(nsp_dense_layer)
    families_in = keras.layers.concatenate([nsp_dense_layer,superkingdoms_out])
    families_out = keras.layers.Dense(families,activation='softmax',name="families_softmax")(families_in)
    species_in = keras.layers.concatenate([nsp_dense_layer,families_out])
    species_out = keras.layers.Dense(species,activation='softmax',name="species_softmax")(species_in)
    out_layer = keras.layers.concatenate([superkingdoms_out,families_out,species_out])

    model_fine = keras.Model(inputs=inputs, outputs=out_layer)
    return model_fine


def seq2tokens(seq, token_dict, seq_length=250, max_length=None,
               k=3, stride=3, window=True, seq_len_like=None):
    """transforms raw sequence into list of tokens to be used for
    fine-tuning BERT
    NOTE: intended to be used as `custom_encode_sequence` argument for
    DataGenerators"""
    if (max_length is None):
        max_length = seq_length
    if (seq_len_like is not None):
        seq_length = min(max_length, np.random.choice(seq_len_like))
        # open('seq_lens.txt', 'a').write(str(seq_length) + ', ')
    seq = seq2kmers(seq, k=k, stride=stride, pad=True)
    if (window):
        start = randint(0, max(len(seq) - seq_length - 1, 0))
        end = start + seq_length - 1
    else:
        start = 0
        end = seq_length
    indices = [token_dict['[CLS]']] + [token_dict[word]
                                       if word in token_dict
                                       else token_dict['[UNK]']
                                       for word in seq[start:end]]
    if (len(indices) < max_length):
        indices += [token_dict['']] * (max_length - len(indices))
    else:
        indices = indices[:max_length]
    segments = [0 for _ in range(max_length)]
    return [np.array(indices), np.array(segments)]


def process_bert_tokens_batch(batch_x):
    """when `seq2tokens` is used as `custom_encode_sequence`, batches
    are generated as [[input1, input2], [input1, input2], ...]. In
    order to train, they have to be transformed to [input1s,
    input2s] with this function"""
    return [np.array([_[0] for _ in batch_x]),
            np.array([_[1] for _ in batch_x])]


def predict(model, test_generator, roc_auc=True, classes=None,
            return_data=False, store_x=False, nonverbose=False):
    from preprocessing.generate_data import PredictGenerator
    predict_g = PredictGenerator(test_generator, store_x=store_x)
    preds = model.predict(predict_g, verbose=0 if nonverbose else 1)
    y = predict_g.get_targets()[:len(preds)] # in case not everything was predicted
    if (len(y) > 0):
        acc = accuracy(y, preds)
        result = [acc]
        metrics_names = ['test_accuracy']
        if model._is_compiled:
            loss_ = loss(y, preds)
            result.append(loss_)
            metrics_names.append('test_loss')
        if roc_auc:
            roc_auc = compute_roc(y, preds, classes).roc_auc
            result.append(roc_auc)
            metrics_names.append('roc_auc')
    else:
        result = []
        metrics_names = []
    return {'metrics': result, 'metrics_names': metrics_names,
            'data': (y, preds) if return_data else None,
            'x': predict_g.get_x() if return_data and store_x else None}

def get_classes_and_weights_multi_tax(species_list, unknown_thr=10_000):
    from utils.tax_entry import TaxidLineage
    tlineage = TaxidLineage()
    
    classes = dict()
    weight_classes = dict()
    super_king_dict = dict()
    king_dict = dict()
    family_dict = dict()
    num_entries = len(species_list)

    for taxid in species_list:
        ranks = tlineage.get_ranks(taxid, ranks=['superkingdom', 'kingdom', 'family'])

        num_same_superking = super_king_dict.get(ranks['superkingdom'][1], 0) + 1
        super_king_dict.update({ranks['superkingdom'][1]: num_same_superking})
        num_same_king = king_dict.get(ranks['kingdom'][1],0) + 1
        king_dict.update({ranks['kingdom'][1]:num_same_king})
        num_same_family = family_dict.get(ranks['family'][1],0) + 1
        family_dict.update({ranks['family'][1]:num_same_family})


    for index, dict_ in enumerate([super_king_dict,king_dict,family_dict]):
        classes_tax_i = dict_.copy()
        unknown = 0
        weight_classes_tax_i = dict()
        for key, value in dict_.items():
            if value < unknown_thr:
                unknown += value
                classes_tax_i.pop(key)
            else:
                weight = num_entries/value
                weight_classes_tax_i.update({key: weight})

        unknown += classes_tax_i.get("unknown", 0)
        classes_tax_i.update({'unknown': unknown})
        classes.update({['superkingdom','kingdom','family'][index]: classes_tax_i})

        weight = num_entries/unknown if unknown != 0 else 1
        weight_classes_tax_i.update({'unknown': weight})
        weight_classes.update({['superkingdom', 'kingdom', 'family'][index]: weight_classes_tax_i})

    return classes, weight_classes
