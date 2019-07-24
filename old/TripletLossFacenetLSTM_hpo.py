import numpy as np
import pandas
import tensorflow as tf
import random as random
import json

from keras import backend as K

from keras.preprocessing.text import Tokenizer
from keras.preprocessing.sequence import pad_sequences

from keras.layers import Dense, Input, Flatten, Dropout, Lambda, GRU, Activation
from keras.layers.wrappers import Bidirectional

from keras.layers import Conv1D, MaxPooling1D, Embedding

from keras.models import Model, model_from_json, Sequential

from embeddings import KazumaCharEmbedding

from annoy import AnnoyIndex

from keras.callbacks import ModelCheckpoint, EarlyStopping

from names_cleanser import NameDataCleanser, CompanyDataCleanser

import sys

import statistics 

import argparse

#must fix
MAX_NB_WORDS = 140000
EMBEDDING_DIM = 100
MAX_SEQUENCE_LENGTH = 10
MARGIN=10
ALPHA=45

DEBUG = False
DEBUG_DATA_LENGTH = 100
DEBUG_ANN = False

USE_ANGULAR_LOSS=False
LOSS_FUNCTION=None
TRAIN_NEIGHBOR_LEN=20
TEST_NEIGHBOR_LEN=20
EMBEDDING_TYPE = 'Kazuma'
NUM_LAYERS = 3
USE_L2_NORM = False

output_file_name_for_hpo = "val_dict_list.json"

def f1score(positive, negative):
    #labels[predictions.ravel() < 0.5].sum()
    fsocre = 0.0
    true_positive = 0.0
    false_positive = 0
    false_negitive = 0
    for i in range(len(positive)):
        if positive[i] <= negative[i]:
            true_positive += 1
        else:
            false_negitive += 1
            false_positive += 1
    print('tp' + str(true_positive))
    print('fp' + str(false_positive))
    print('fn' + str(false_negitive))
    fscore = (2 * true_positive) / ((2 * true_positive) + false_negitive + false_positive)
    return fscore 


def get_embedding_layer(tokenizer):
    word_index = tokenizer.word_index
    num_words = len(word_index) + 1
    embedding_matrix = np.zeros((num_words, EMBEDDING_DIM))
    print('about to get kz')
    kz = KazumaCharEmbedding()
    print('got kz')
    for word, i in word_index.items():

        if i >= MAX_NB_WORDS:
            continue
        embedding_vector = kz.emb(word)

        if embedding_vector is not None:
            if sum(embedding_vector) == 0:
                print("failed to find embedding for:" + word)
            # words not found in embedding index will be all-zeros.
            embedding_matrix[i] = embedding_vector
    print("Number of words:" + str(num_words))

    embedding_layer = Embedding(num_words,

                                EMBEDDING_DIM,

                                weights=[embedding_matrix],

                                input_length=MAX_SEQUENCE_LENGTH,

                                trainable=False)
    return embedding_layer

def get_sequences(texts, tokenizer):
    sequences = {}
    sequences['anchor'] = tokenizer.texts_to_sequences(texts['anchor'])
    sequences['anchor'] = pad_sequences(sequences['anchor'], maxlen=MAX_SEQUENCE_LENGTH)
    sequences['negative'] = tokenizer.texts_to_sequences(texts['negative'])
    sequences['negative'] = pad_sequences(sequences['negative'], maxlen=MAX_SEQUENCE_LENGTH)
    sequences['positive'] = tokenizer.texts_to_sequences(texts['positive'])
    sequences['positive'] = pad_sequences(sequences['positive'], maxlen=MAX_SEQUENCE_LENGTH)

    return sequences

def read_entities(filepath):
    entities = []
    with open(filepath) as fl:
        for line in fl:
            entities.append(line)

    return entities

def read_file(file_path):
    texts = {'anchor':[], 'negative':[], 'positive':[]}
    fl = open(file_path, 'r')
    i = 0
    for line in fl:
        line_array = line.split("|")
        texts['anchor'].append(line_array[0])
        texts['positive'].append(line_array[1])
        texts['negative'].append(line_array[2])
        i += 1
        if i > DEBUG_DATA_LENGTH and DEBUG:
            break
    return texts

def split(entities, test_split = 0.2):
    if DEBUG:
        ents = entities[0:DEBUG_DATA_LENGTH]
    else:
        random.shuffle(entities)
        ents = entities
    num_validation_samples = int(test_split * len(ents))
    return ents[:-num_validation_samples], ents[-num_validation_samples:]

"""
  define a single objective function based on angular loss instead of triplet loss
"""
def angular_loss(y_true, y_pred):
    alpha = K.constant(ALPHA)
    a_p = y_pred[:,0,0]
    n_c = y_pred[:,1,0]
    return K.mean(K.maximum(K.constant(0), K.square(a_p) - K.constant(4) * K.square(tf.tan(alpha)) * K.square(n_c)))
 

"""
    Facenet triplet loss function: https://arxiv.org/pdf/1503.03832.pdf
"""
def schroff_triplet_loss(y_true, y_pred):
    margin = K.constant(0.2)
    return K.mean(K.maximum(K.constant(0), K.square(y_pred[:,0,0]) - K.square(y_pred[:,1,0]) + margin))

def triplet_loss(y_true, y_pred):
    margin = K.constant(MARGIN)
    return K.mean(K.square(y_pred[:,0,0]) + K.square(margin - y_pred[:,1,0]))

def triplet_tanh_loss(y_true, y_pred):
    return K.mean(K.tanh(y_pred[:,0,0]) + (K.constant(1) - K.tanh(y_pred[:,1,0])))

def triplet_tanh_pn_loss(y_true, y_pred):
    return K.mean(K.tanh(y_pred[:,0,0]) +
                  ((K.constant(1) - K.tanh(y_pred[:,1,0])) +
                   (K.constant(1) - K.tanh(y_pred[:,2,0]))) / K.constant(2));


# the following triplet loss function is from: Deep Metric Learning with Improved Triplet Loss for 
# Face clustering in Videos 
def improved_loss(y_true, y_pred):
    margin = K.constant(1)
    lambda_p = K.constant(0.02)
    threshold = K.constant(0.1)
    a_p_distance = y_pred[:,0,0]
    a_n_distance = y_pred[:,1,0]
    p_n_distance = y_pred[:,2,0]
    phi = a_p_distance - ((a_n_distance + p_n_distance) / K.constant(2)) + margin
    psi = a_p_distance - threshold 

    return K.maximum(K.constant(0), phi) + lambda_p * K.maximum(K.constant(0), psi) 

def accuracy(y_true, y_pred):
    return K.mean(y_pred[:,0,0]  < y_pred[:,1,0])

def l2Norm(x):
    return K.l2_normalize(x, axis=-1)

def euclidean_distance(vects):
    x, y = vects
    return K.sqrt(K.maximum(K.sum(K.square(x - y), axis=1, keepdims=True), K.epsilon()))

def n_c_angular_distance(vects):
    x_a, x_p, x_n = vects
    return K.sqrt(K.maximum(K.sum(K.square(x_n - ((x_a + x_p) / K.constant(2))), axis=1, keepdims=True), K.epsilon()))

def a_p_angular_distance(vects):
    x_a, x_p, x_n = vects
    return K.sqrt(K.maximum(K.sum(K.square(x_a - x_p), axis=1, keepdims=True), K.epsilon()))

def build_unique_entities(entity2same):
    unique_text = []
    entity2index = {}

    for key in entity2same:
        entity2index[key] = len(unique_text)
        unique_text.append(key)
        vals = entity2same[key]
        for v in vals:
            entity2index[v] = len(unique_text)
            unique_text.append(v)

    return unique_text, entity2index


def generate_triplets_from_ANN(model, sequences, entity2unique, entity2same, unique_text, test):
    predictions = model.predict(sequences)
    t = AnnoyIndex(len(predictions[0]), metric='euclidean')  # Length of item vector that will be indexed
    t.set_seed(123)
    for i in range(len(predictions)):
        # print(predictions[i])
        v = predictions[i]
        t.add_item(i, v)

    t.build(100) # 100 trees

    match = 0
    no_match = 0
    accuracy = 0
    total = 0

    triplets = {}

    pos_distances = []
    neg_distances = []

    triplets['anchor'] = []
    triplets['positive'] = []
    triplets['negative'] = []

    if test:
        NNlen = TEST_NEIGHBOR_LEN
    else:
        NNlen = TRAIN_NEIGHBOR_LEN

    for key in entity2same:
        index = entity2unique[key]
        nearest = t.get_nns_by_vector(predictions[index], NNlen)
        nearest_text = set([unique_text[i] for i in nearest])
        expected_text = set(entity2same[key])
        # annoy has this annoying habit of returning the queried item back as a nearest neighbor.  Remove it.
        if key in nearest_text:
            nearest_text.remove(key)
        # print("query={} names = {} true_match = {}".format(unique_text[index], nearest_text, expected_text))
        overlap = expected_text.intersection(nearest_text)
        # collect up some statistics on how well we did on the match
        m = len(overlap)
        match += m
        # since we asked for only x nearest neighbors, and we get at most x-1 neighbors that are not the same as key (!)
        # make sure we adjust our estimate of no match appropriately
        no_match += min(len(expected_text), NNlen - 1) - m

        # sample only the negatives that are true negatives
        # that is, they are not in the expected set - sampling only 'semi-hard negatives is not defined here'
        # positives = expected_text - nearest_text
        positives = expected_text
        negatives = nearest_text - expected_text

        # print(key + str(expected_text) + str(nearest_text))
        for i in negatives:
            for j in positives:
                dist_pos = t.get_distance(index, entity2unique[j])
                pos_distances.append(dist_pos)
                dist_neg = t.get_distance(index, entity2unique[i])
                neg_distances.append(dist_neg)
                if dist_pos < dist_neg:
                    accuracy += 1
                total += 1
                # print(key + "|" +  j + "|" + i)
                # print(dist_pos)
                # print(dist_neg)               
                triplets['anchor'].append(key)
                triplets['positive'].append(j)
                triplets['negative'].append(i)

    print("mean positive distance:" + str(statistics.mean(pos_distances)))
    print("stdev positive distance:" + str(statistics.stdev(pos_distances)))
    print("max positive distance:" + str(max(pos_distances)))
    print("mean neg distance:" + str(statistics.mean(neg_distances)))
    print("stdev neg distance:" + str(statistics.stdev(neg_distances)))
    print("max neg distance:" + str(max(neg_distances)))
    print("Accuracy in the ANN for triplets that obey the distance func:" + str(accuracy / total))

    obj = {}
    obj['accuracy'] = accuracy / total
    obj['steps'] = 1
    with open(output_file_name_for_hpo, 'w') as out:
        json.dump(obj, out)

    if test:
        return match/(match + no_match)
    else:
        return triplets, match/(match + no_match)


def generate_names(entities, people, limit_pairs=False):
    if people:
        num_names = 4
        generator = NameDataCleanser(0, num_names, limit_pairs=limit_pairs)
    else:
        generator = CompanyDataCleanser(limit_pairs)
        num_names = 2

    entity2same = {}
    for entity in entities:
        ret = generator.cleanse_data(entity)
        if ret and len(ret) >= num_names:
            entity2same[ret[0]] = ret[1:]
    return entity2same


def embedded_representation_model(embedding_layer):
    seq = Sequential()
    seq.add(embedding_layer)
    seq.add(Flatten())
    return seq

def build_model(embedder):
    main_input = Input(shape=(MAX_SEQUENCE_LENGTH,))
    net = embedder(main_input)

    for i in range(0, NUM_LAYERS):
        net = GRU(128, return_sequences=True, activation='relu', name='embed' + str(i))(net)
    net = GRU(128, activation='relu', name='embed' + str(i+1))(net)
    
    if USE_L2_NORM:
        net = Lambda(l2Norm, output_shape=[128])(net)

    base_model = Model(embedder.input, net, name='triplet_model')

    base_model.summary()

    input_shape=(MAX_SEQUENCE_LENGTH,)
    input_anchor = Input(shape=input_shape, name='input_anchor')
    input_positive = Input(shape=input_shape, name='input_pos')
    input_negative = Input(shape=input_shape, name='input_neg')
    net_anchor = base_model(input_anchor)
    net_positive = base_model(input_positive)
    net_negative = base_model(input_negative)
    positive_dist = Lambda(euclidean_distance, name='pos_dist', output_shape=(1,))([net_anchor, net_positive])
    negative_dist = Lambda(euclidean_distance, name='neg_dist', output_shape=(1,))([net_anchor, net_negative])
 
    if USE_ANGULAR_LOSS:
        n_c = Lambda(n_c_angular_distance, name='nc_angular_dist')([net_anchor, net_positive, net_negative])
        a_p = Lambda(a_p_angular_distance, name='ap_angular_dist')([net_anchor, net_positive, net_negative])
        stacked_dists = Lambda( 
                    lambda vects: K.stack(vects, axis=1),
                    name='stacked_dists', output_shape=(3, 1)
                    )([a_p, n_c])
        model = Model([input_anchor, input_positive, input_negative], stacked_dists, name='triple_siamese')
        model.compile(optimizer="rmsprop", loss=angular_loss, metrics=[accuracy])
    else:
        exemplar_negative_dist = Lambda(euclidean_distance, name='exemplar_neg_dist', output_shape=(1,))([net_positive, net_negative])
        stacked_dists = Lambda( 
                   # lambda vects: C.splice(*vects, axis=C.Axis.new_leading_axis()).eval(vects),
                    lambda vects: K.stack(vects, axis=1),
                    name='stacked_dists', output_shape=(3, 1)
                    )([positive_dist, negative_dist, exemplar_negative_dist])

        model = Model([input_anchor, input_positive, input_negative], stacked_dists, name='triple_siamese')
        model.compile(optimizer="rmsprop", loss=LOSS_FUNCTION, metrics=[accuracy])
    test_positive_model = Model([input_anchor, input_positive, input_negative], positive_dist)
    test_negative_model = Model([input_anchor, input_positive, input_negative], negative_dist)
    inter_model = Model(input_anchor, net_anchor)
    print("output_shapes")
    model.summary()
    # print(positive_dist.output_shape)
    # print(negative_dist.output_shape)
    # print(exemplar_negative_dist)
    # print(neg_dist.output_shape)

    return model, test_positive_model, test_negative_model, inter_model


parser = argparse.ArgumentParser(description='Run fuzzy join algorithm')
parser.add_argument('--debug_sample_size', type=int,
                    help='sample size for debug run')
parser.add_argument('--margin',  type=int,
                    help='margin')
parser.add_argument('--loss_function',  type=str,
                    help='triplet loss function type: schroff-loss, improved-loss, angular-loss, tanh-loss, improved-tanh-loss')
parser.add_argument('--use_l2_norm',  type=str,
                    help='whether to add a l2 norm')
parser.add_argument('--num_layers', type=int,
                   help='num_layers to use.  Minimum is 2')
parser.add_argument('--input', type=str, help='Input file')

parser.add_argument('--entity_type', type=str, help='people or companies')


args = parser.parse_args()

LOSS_FUNCTION = None
if args.loss_function == 'schroff-loss':
    LOSS_FUNCTION=schroff_triplet_loss
elif args.loss_function == 'improved-loss':
    LOSS_FUNCTION=improved_loss
elif args.loss_function == 'our-loss':
    LOSS_FUNCTION=triplet_loss
elif args.loss_function == 'tanh-loss':
    LOSS_FUNCTION=triplet_tanh_loss
elif args.loss_function == 'improved-tanh-loss':
    LOSS_FUNCTION=triplet_tanh_pn_loss
elif args.loss_function == 'angular-loss':
    USE_ANGULAR_LOSS = True
    LOSS_FUNCTION = angular_loss
print('Loss function: ' + args.loss_function)

if args.debug_sample_size:
    DEBUG=True
    DEBUG_DATA_LENGTH=args.debug_sample_size
    print('Debug data length:' + str(DEBUG_DATA_LENGTH))

print('Margin:' + str(MARGIN))

USE_L2_NORM = args.use_l2_norm.lower() in ("yes", "true", "t", "1")
print('Use L2Norm: ' + str(USE_L2_NORM)) 
print('Use L2Norm: ' + str(args.use_l2_norm)) 

NUM_LAYERS = args.num_layers - 1
print('Num layers: ' + str(NUM_LAYERS))

people = 'people' in args.entity_type

# read all entities and create positive parts of a triplet
entities = read_entities(args.input)
train, test = split(entities, test_split = .20)
print("TRAIN")
print(train)
print("TEST")
print(test)

entity2same_train = generate_names(train, people)
entity2same_test = generate_names(test, people, limit_pairs=True)
print(entity2same_train)
print(entity2same_test)

# change the default behavior of the tokenizer to ignore all punctuation except , - and . which are important
# clues for entity names
tokenizer = Tokenizer(num_words=MAX_NB_WORDS, lower=True, filters='!"#$%&()*+/:;<=>?@[\]^_`{|}~', split=" ")   

# build a set of data structures useful for annoy, the set of unique entities (unique_text), 
# a mapping of entities in texts to an index in unique_text, a mapping of entities to other same entities, and the actual
# vectorized representation of the text.  These structures will be used iteratively as we build up the model
# so we need to create them once for re-use
unique_text, entity2unique = build_unique_entities(entity2same_train)
unique_text_test, entity2unique_test = build_unique_entities(entity2same_test)

print("train text len:" + str(len(unique_text)))
print("test text len:" + str(len(unique_text_test)))

tokenizer.fit_on_texts(unique_text + unique_text_test)

sequences = tokenizer.texts_to_sequences(unique_text)
sequences = pad_sequences(sequences, maxlen=MAX_SEQUENCE_LENGTH)
sequences_test = tokenizer.texts_to_sequences(unique_text_test)
sequences_test = pad_sequences(sequences_test, maxlen=MAX_SEQUENCE_LENGTH)

# build models
embedder = get_embedding_layer(tokenizer)
model, test_positive_model, test_negative_model, inter_model = build_model(embedder)
embedder_model = embedded_representation_model(embedder)


if DEBUG_ANN:
    generate_triplets_from_ANN(embedder_model, sequences_test, entity2unique_test, entity2same_test, unique_text_test, True)
    sys.exit()

test_data, test_match_stats = generate_triplets_from_ANN(embedder_model, sequences_test, entity2unique_test, entity2same_test, unique_text_test, False)
test_seq = get_sequences(test_data, tokenizer)
print("Test stats:" + str(test_match_stats))

counter = 0
current_model = embedder_model
prev_match_stats = 0

train_data, match_stats = generate_triplets_from_ANN(current_model, sequences, entity2unique, entity2same_train, unique_text, False)
print("Match stats:" + str(match_stats))

number_of_names = len(train_data['anchor'])
# print(train_data['anchor'])
print("number of names" + str(number_of_names))
Y_train = np.random.randint(2, size=(1,2,number_of_names)).T

filepath="weights.best.hdf5"
checkpoint = ModelCheckpoint(filepath, monitor='val_accuracy', verbose=1, save_best_only=True, mode='max')

early_stop = EarlyStopping(monitor='val_accuracy', patience=1, mode='max')

callbacks_list = [checkpoint, early_stop]

train_seq = get_sequences(train_data, tokenizer)

# check just for 5 epochs because this gets called many times
model.fit([train_seq['anchor'], train_seq['positive'], train_seq['negative']], Y_train, epochs=100,  batch_size=40, callbacks=callbacks_list, validation_split=0.2)
current_model = inter_model
# print some statistics on this epoch

print("training data predictions")
positives = test_positive_model.predict([train_seq['anchor'], train_seq['positive'], train_seq['negative']])
negatives = test_negative_model.predict([train_seq['anchor'], train_seq['positive'], train_seq['negative']])
print("f1score for train is: {}".format(f1score(positives, negatives)))
print("test data predictions")
positives = test_positive_model.predict([test_seq['anchor'], test_seq['positive'], test_seq['negative']])
negatives = test_negative_model.predict([test_seq['anchor'], test_seq['positive'], test_seq['negative']])
print("f1score for test is: {}".format(f1score(positives, negatives)))


test_match_stats = generate_triplets_from_ANN(current_model, sequences_test, entity2unique_test, entity2same_test, unique_text_test, True)
print("Test stats:" + str(test_match_stats))
