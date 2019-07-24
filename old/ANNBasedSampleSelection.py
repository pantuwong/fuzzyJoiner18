import Named_Entity_Recognition_Modified
from keras.preprocessing.text import Tokenizer
from keras.preprocessing.sequence import pad_sequences
from embeddings import KazumaCharEmbedding
from annoy import AnnoyIndex
from matcher_functions import connect
import argparse
import numpy as np
from keras.layers import Embedding, Concatenate
from keras.models import Model
import names_cleanser
from random import randint
import keras.backend as K
import sys

MARGIN = 2
DEBUG = False

def process_aliases(con, meta):
	aliases = Named_Entity_Recognition_Modified.get_aliases_with_ids(con, meta)
	entity2sames = {}
	namesToIds = {}

	def has_difft_id(name, entityid):
		if name in namesToIds:
			ids = namesToIds[name]
			if ids != entityid:
				return True
		namesToIds[name] = entityid
		return False


	i = 0
	for row in aliases:
		i += 1
		if DEBUG and i > 100:
			break
		entityid = row[2]
		# filter out names that are associated with multiple ids, this will confuse the model trying to learn the distance function
		if has_difft_id(row[0], entityid) or has_difft_id(row[1], entityid) or has_difft_id(row[2], entityid):
			continue

		if entityid not in entity2sames:
			entity2sames[entityid] = [row[0]]
			entity2sames[entityid].append(row[1])
		else:
			entity2sames[entityid].append(row[1])

	# print(entity2sames)
	entities = []
	entity2names = {}

	for e,v in entity2sames.items():
		entity2names[e] = [len(entities) + i for i in range(0, len(v))]
		entities.extend(v)
	
	print(entities)
	print(entity2names)
	return entities, entity2names


if __name__ == '__main__':
    print('Processing text dataset')

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-u', dest="user", help="username")
    parser.add_argument('-p', dest="password", help="password")
    parser.add_argument('-d', dest="db", help="dbname")
    parser.add_argument('-o', dest="output_file", help="output file name")

    parser.add_argument('-a', dest="num_pairs", help="number of same pairs in db", nargs='?', default=2, type=int)

    args = parser.parse_args()

    #change to get from sql and not read from file
    con, meta = connect(args.user, args.password, args.db)
 
    # get all names first
    entities, entity2names = process_aliases(con, meta)
    tokenizer = Tokenizer(num_words=Named_Entity_Recognition_Modified.MAX_NB_WORDS)
    tokenizer.fit_on_texts(entities)
    sequences = tokenizer.texts_to_sequences(entities)
    print(sequences)
 
    sequences = pad_sequences(sequences, maxlen=Named_Entity_Recognition_Modified.MAX_SEQUENCE_LENGTH)
 
    word_index = tokenizer.word_index
    num_words = len(word_index) + 1
    embedding_matrix = np.zeros((num_words, Named_Entity_Recognition_Modified.EMBEDDING_DIM))
    kz = KazumaCharEmbedding()

    for word, i in word_index.items():
        if i >= Named_Entity_Recognition_Modified.MAX_NB_WORDS:
            continue
        embedding_vector = kz.emb(word)

        if embedding_vector is not None:
            if sum(embedding_vector) == 0:
                print("failed to find embedding for:" + word)
            # words not found in embedding index will be all-zeros.
            embedding_matrix[i] = embedding_vector

    # note that we set trainable = False so as to keep the embeddings fixed
    Named_Entity_Recognition_Modified.check_for_zeroes(embedding_matrix, "here is the first pass")
    embedding_layer = Embedding(num_words,

                                Named_Entity_Recognition_Modified.EMBEDDING_DIM,

                                weights=[embedding_matrix],

                                input_length=Named_Entity_Recognition_Modified.MAX_SEQUENCE_LENGTH,

                                trainable=False)

    model = Named_Entity_Recognition_Modified.embedded_representation(embedding_layer)

    embedded_output = model.predict(sequences)
    print(np.shape(embedded_output))
    sys.exit(0)

    t = AnnoyIndex(len(embedded_output[0]), metric='euclidean')

    for i in range(len(embedded_output)):
    	v = embedded_output[i]
    	t.add_item(i, v)

    t.build(100) # 100 trees

    with open(args.output_file, 'w') as f:
    	
    	for e, v in entity2names.items():
	    	index_for_same = entity2names[e]
	    	anchor_index = index_for_same[0]
	    	nearest = t.get_nns_by_vector(embedded_output[anchor_index], 10)
	    	maximum_diff = -1
	    	minimum_same = 100000
	    	maximum_same = -1
	    	same_pair_in_NN_set = False

	    	for i in range(1, len(index_for_same)):
	    		dist = t.get_distance(anchor_index,  index_for_same[i])
	    		print("same pair:" + entities[anchor_index] + "-" + entities[index_for_same[i]] + " distance:" + str(dist))
	    		minimum_same = min(dist, minimum_same)
	    		maximum_same = max(dist, maximum_same)

	    	for i in nearest:
	    		if i == anchor_index:
	    			continue
	    		dist = t.get_distance(anchor_index, i)
	    		print(entities[anchor_index] + "-" + entities[i] + " distance:" + str(dist))
	  
	    		if i in index_for_same:
	    			same_pair_in_NN_set = True
	    		else:
	    			maximum_diff = max(dist, maximum_diff)
	    			if dist > minimum_same:
	    				f.write(entities[anchor_index] + "|" + entities[index_for_same[randint(1, len(index_for_same) - 1)]] + "|" + entities[i] + "\n")

	    	if (maximum_diff < minimum_same):
	    		print("hard entity because maximum different is less than minimum same")
	    		continue
	    	elif same_pair_in_NN_set:
	    		print("easy entity - same pair is in NN set")
	    	else:
	    		print("~hard entity" + entities[anchor_index])

	    	# write a set of different completely items now
	    	
	    	print(maximum_same)
	    	j = 0
	    	while j <= 30:
	    		k = randint(0, len(entities) - 1)
	    		if t.get_distance(anchor_index,  k) > maximum_diff + MARGIN:
	    			f.write((entities[anchor_index] + "|" + entities[index_for_same[randint(1, len(index_for_same) - 1)]] + "|" + entities[k] + "\n"))
	    			k += 1
	    		j += 1
	    	


    print(len(entity2names))
 
