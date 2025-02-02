"""Main module."""

import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers
from tensorflow.keras.layers.experimental.preprocessing import TextVectorization
import tqdm
import pickle
import numpy as np

class ProdB():

    class MaskedLanguageModel(tf.keras.Model):

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.loss_fn = keras.losses.SparseCategoricalCrossentropy(
                reduction=tf.keras.losses.Reduction.NONE
            )
            self.loss_tracker = tf.keras.metrics.Mean(name="loss")

        def train_step(self, inputs):
            if len(inputs) == 3:
                features, labels, sample_weight = inputs
            else:
                features, labels = inputs
                sample_weight = None

            with tf.GradientTape() as tape:
                predictions = self(features, training=True)
                loss = self.loss_fn(labels, predictions, sample_weight=sample_weight)

            # Compute gradients
            trainable_vars = self.trainable_variables
            gradients = tape.gradient(loss, trainable_vars)

            # Update weights
            self.optimizer.apply_gradients(zip(gradients, trainable_vars))

            # Compute our own metrics
            self.loss_tracker.update_state(loss, sample_weight=sample_weight)

            # Return a dict mapping metric names to current value
            return {"loss": self.loss_tracker.result()}

        @property
        def metrics(self):
            # We list our `Metric` objects here so that `reset_states()` can be
            # called automatically at the start of each epoch
            # or at the start of `evaluate()`.
            # If you don't implement this property, you have to call
            # `reset_states()` yourself at the time of your choosing.
            return [self.loss_tracker]

    def __repr__(self):
        return "EMB_DIM_{config.EMBED_DIM}_EPOCHS_{config.EPOCHS}_NUM_LAYERS_{config.NUM_LAYERS}_DATA_RATIO_{config.DATA_RATIO}_MASKING_PROBABILITY_{config.MASKING_PROBABILITY}".format(config=self.config)

    def __str__(self):
        return "EMB_DIM_{config.EMBED_DIM}_EPOCHS_{config.EPOCHS}_NUM_LAYERS_{config.NUM_LAYERS}_DATA_RATIO_{config.DATA_RATIO}_MASKING_PROBABILITY_{config.MASKING_PROBABILITY}".format(config=self.config)

    def __init__(self, sessions, config):
        self.sessions = sessions
        self.config = config
        self.vectorize_layer = self.get_vectorize_layer(
            sessions,
            special_tokens=["mask"],
        )

        # Get mask token id for masked language model
        self.mask_token_id = self.vectorize_layer(["mask"]).numpy()[0][0]

        # Prepare data for masked language model
        x_all_review = self.encode(sessions)
        x_masked_train, y_masked_labels, sample_weights = self.get_masked_input_and_labels(
            x_all_review
        )

        mlm_ds = tf.data.Dataset.from_tensor_slices(
            (x_masked_train, y_masked_labels, sample_weights)
        )
        self.mlm_ds = mlm_ds.shuffle(1000).batch(self.config.BATCH_SIZE)

        self.id2token = dict(enumerate(self.vectorize_layer.get_vocabulary()))
        self.token2id = {y: x for x, y in self.id2token.items()}

        self.bert_masked_model = self.create_masked_language_bert_model()
        #bert_masked_model.summary()

    def __call__(self, *args, **kwargs):
        self.bert_masked_model.fit(self.mlm_ds, epochs=self.config.EPOCHS, callbacks=kwargs.get('callbacks', None))
        # self.bert_masked_model.save(self.__str__() + ".h5")
        self.bert_masked_model.save(self.__str__() + ".keras") # TODO: # Fix: UserWarning: You are saving your model as an HDF5 file via `model.save()`. This file format is considered legacy. We recommend using instead the native Keras format, e.g. `model.save('my_model.keras')`.

        vectorizer = self.vectorize_layer

        # Pickle the config and weights
        pickle.dump({'config': vectorizer.get_config(),
                     'weights': vectorizer.get_weights()}
                    , open(self.__str__() +"_TEXT_VECTORIZER.pkl", "wb"))

        pickle.dump({
                "id2token" : self.id2token,
                "token2id" :  self.token2id,
                "config" : self.config
            }, open(self.__str__() + "_CONFIG.pkl", "wb"))


        """
        to load

        from_disk = pickle.load(open("tv_layer.pkl", "rb"))
        new_v = TextVectorization.from_config(from_disk['config'])
        # You have to call `adapt` with some dummy data (BUG in Keras)
        new_v.adapt(tf.data.Dataset.from_tensor_slices(["xyz"]))
        new_v.set_weights(from_disk['weights'])
        """

    def load_weights(self, base_path): # Added ability to load weights
        self.bert_masked_model = keras.models.load_model(base_path + self.__str__() + ".keras") # TODO: # warning save as keras instead of deprecated h5
        # self.bert_masked_model = keras.models.load_model(base_path + self.__str__() + ".h5")
        self.vectorize_layer = pickle.load(open(base_path + self.__str__() +"_TEXT_VECTORIZER.pkl", "rb"))
        self.id2token = pickle.load(open(base_path + self.__str__() + "_CONFIG.pkl", "rb"))['id2token']
        self.token2id = pickle.load(open(base_path + self.__str__() + "_CONFIG.pkl", "rb"))['token2id']

    def encode(self, texts):
        encoded_texts = self.vectorize_layer(texts)
        return encoded_texts.numpy()

    def get_masked_input_and_labels(self, encoded_texts):
        # 15% BERT masking
        inp_mask = np.random.rand(*encoded_texts.shape) < self.config.MASKING_PROBABILITY
        # Do not mask special tokens
        inp_mask[encoded_texts <= 2] = False
        # Set targets to -1 by default, it means ignore
        labels = -1 * np.ones(encoded_texts.shape, dtype=int)
        # Set labels for masked tokens
        labels[inp_mask] = encoded_texts[inp_mask]

        # Prepare input
        encoded_texts_masked = np.copy(encoded_texts)
        # Set input to [MASK] which is the last token for the 90% of tokens
        # This means leaving 10% unchanged
        inp_mask_2mask = inp_mask & (np.random.rand(*encoded_texts.shape) < 0.90)
        encoded_texts_masked[
            inp_mask_2mask
        ] = self.mask_token_id  # mask token is the last in the dict

        # Set 10% to a random token
        inp_mask_2random = inp_mask_2mask & (np.random.rand(*encoded_texts.shape) < 1 / 9)
        encoded_texts_masked[inp_mask_2random] = np.random.randint(
            3, self.mask_token_id, inp_mask_2random.sum()
        )

        # Prepare sample_weights to pass to .fit() method
        sample_weights = np.ones(labels.shape)
        sample_weights[labels == -1] = 0

        # y_labels would be same as encoded_texts i.e input tokens
        y_labels = np.copy(encoded_texts)

        return encoded_texts_masked, y_labels, sample_weights

    def bert_module(self, query, key, value, i):
        # Multi headed self-attention
        attention_output = layers.MultiHeadAttention(
            num_heads=self.config.NUM_HEAD,
            key_dim=self.config.EMBED_DIM // self.config.NUM_HEAD,
            name="encoder_{}/multiheadattention".format(i),
        )(query, key, value)
        attention_output = layers.Dropout(0.1, name="encoder_{}/att_dropout".format(i))(
            attention_output
        )
        attention_output = layers.LayerNormalization(
            epsilon=1e-6, name="encoder_{}/att_layernormalization".format(i)
        )(query + attention_output)

        # Feed-forward layer
        ffn = keras.Sequential(
            [
                layers.Dense(self.config.FF_DIM, activation="relu"),
                layers.Dense(self.config.EMBED_DIM, name="encoder_{}/simple".format(i)),
            ],
            name="encoder_{}/ffn".format(i),
        )
        ffn_output = ffn(attention_output)
        ffn_output = layers.Dropout(0.1, name="encoder_{}/ffn_dropout".format(i))(
            ffn_output
        )
        sequence_output = layers.LayerNormalization(
            epsilon=1e-6, name="encoder_{}/ffn_layernormalization".format(i)
        )(attention_output + ffn_output)
        return sequence_output

    def get_pos_encoding_matrix(self, max_len, d_emb):
        pos_enc = np.array(
            [
                [pos / np.power(10000, 2 * (j // 2) / d_emb) for j in range(d_emb)]
                if pos != 0
                else np.zeros(d_emb)
                for pos in range(max_len)
            ]
        )
        pos_enc[1:, 0::2] = np.sin(pos_enc[1:, 0::2])  # dim 2i
        pos_enc[1:, 1::2] = np.cos(pos_enc[1:, 1::2])  # dim 2i+1
        return pos_enc

    def create_masked_language_bert_model(self):
        inputs = layers.Input((self.config.MAX_LEN,), dtype=tf.int64)

        word_embeddings = layers.Embedding(
            self.config.VOCAB_SIZE, self.config.EMBED_DIM, name="word_embedding"
        )(inputs)
        position_embeddings = layers.Embedding(
            input_dim=self.config.MAX_LEN,
            output_dim=self.config.EMBED_DIM,
            weights=[self.get_pos_encoding_matrix(self.config.MAX_LEN, self.config.EMBED_DIM)],
            name="position_embedding",
        )(tf.range(start=0, limit=self.config.MAX_LEN, delta=1))
        embeddings = word_embeddings + position_embeddings

        encoder_output = embeddings
        for i in range(self.config.NUM_LAYERS):
            encoder_output = self.bert_module(encoder_output, encoder_output, encoder_output, i)

        mlm_output = layers.Dense(self.config.VOCAB_SIZE, name="mlm_cls", activation="softmax")(
            encoder_output
        )
        mlm_model = self.MaskedLanguageModel(inputs, mlm_output, name="masked_bert_model")

        # optimizer = keras.optimizers.Adam(learning_rate=self.config.LR)
        optimizer = keras.optimizers.legacy.Adam(learning_rate=self.config.LR) # Fix: WARNING:absl:At this time, the v2.11+ optimizer `tf.keras.optimizers.Adam` runs slowly on M1/M2 Macs, please use the legacy Keras optimizer instead, located at `tf.keras.optimizers.legacy.Adam`.
        mlm_model.compile(optimizer=optimizer)
        return mlm_model

    def decode(self, tokens):
        return " ".join([self.id2token[t] for t in tokens if t != 0])

    def convert_ids_to_tokens(self, id):
        return self.id2token[id]


    def get_embeddings_for_sessions(self, encoder_layer, sessions, pooling="average", output_layer_name = "normalization", make_average=True):
        """
        This method provides different ways of extracting a session embedding given in input a sequence of sessions
        """
        if output_layer_name == "normalization":
            output_layer =  self.bert_masked_model.get_layer("encoder_" + str(encoder_layer) + "/ffn_layernormalization").output
        elif output_layer_name == "simple":
            output_layer = self.bert_masked_model.get_layer("encoder_" + str(encoder_layer) + "/multiheadattention").output
        else:
            raise Exception("Non valid output layer name")

        pretrained_bert_model = tf.keras.Model(
            self.bert_masked_model.input, output_layer
        )
        pretrained_bert_model.trainable = False
        collect_embeddings = []
        pbar = tqdm.tqdm(total=(len(sessions)))
        for sess in sessions:
            k = self.vectorize_layer([sess])
            # embeddings = (pretrained_bert_model.predict(k)[0])
            embeddings = (pretrained_bert_model.predict(k, verbose=0)[0])
            sample_length = len(sess.split())
            embeddings = embeddings[0:sample_length]
            if make_average:
                collect_embeddings.append(np.average(embeddings, axis=0))
            else:
                collect_embeddings.append(embeddings)
            pbar.update(1)
        pbar.close()
        return collect_embeddings

    def get_last_embeddings_for_sessions(self, encoder_layer, sessions, pooling="average", output_layer_name = "normalization"):

        if output_layer_name == "normalization":
            output_layer =  self.bert_masked_model.get_layer("encoder_" + str(encoder_layer) + "/ffn_layernormalization").output
        elif output_layer_name == "simple":
            output_layer = self.bert_masked_model.get_layer("encoder_" + str(encoder_layer) + "/multiheadattention").output
        else:
            raise Exception("Non valid output layer name")


        pretrained_bert_model = tf.keras.Model(
            self.bert_masked_model.input, output_layer
        )
        pretrained_bert_model.trainable = False
        collect_embeddings = []
        pbar = tqdm.tqdm(total=(len(sessions)))
        for sess in sessions:
            sess = sess + " mask"
            k = self.vectorize_layer([sess])
            # embeddings = (pretrained_bert_model.predict(k)[0])
            embeddings = (pretrained_bert_model.predict(k, verbose=0)[0])
            sample_length = len(sess.split())
            embeddings = embeddings[sample_length - 1]
            collect_embeddings.append(embeddings)
            pbar.update(1)
        pbar.close()
        return collect_embeddings

    def run_next_item_predictions(self, sessions, subword_prefix=None):
        """
        Run the NEXT Item Prediction task
        """
        gt = []
        # pbar = tqdm.tqdm(total=(len(sessions)))
        # pbar = tqdm.notebook.tqdm(total=(len(sessions))) # TODO: test if this fixes printing multiple times
        predictions = []

        for index, a in enumerate(sessions):
            splitted = a.split()

            if subword_prefix is not None:
                for index in range(0, len(splitted)):
                    real_index = len(splitted) - 1 - index
                    if not splitted[real_index].startswith(subword_prefix):
                        splitted = splitted[:real_index+1]
                        break

            to_predict = splitted[-1]
            splitted[-1] = "mask"
            joined = " ".join(splitted)
            gt.append(to_predict)
            predictions.append(self.predict_from_tokens(joined))
            # pbar.update(1)
        # pbar.close()

        return {"ground" : gt, "top_10_predictions" : predictions}

    def predict_from_tokens(self, string_ids):
        sample_tokens = self.vectorize_layer([string_ids])

        # prediction = self.bert_masked_model.predict(sample_tokens)
        prediction = self.bert_masked_model.predict(sample_tokens, verbose=0)
        masked_index = np.where(sample_tokens == self.mask_token_id)
        masked_index = masked_index[1]
        mask_prediction = prediction[0][masked_index]

        top_indices = mask_prediction[0].argsort()[-10:][::-1]
        # top_indices = mask_prediction[0].argsort()[-100:][::-1] # TODO: added to get top 100 predictions instead of 10
        values = mask_prediction[0][top_indices]

        answers = []
        for i in range(len(top_indices)):
            p = top_indices[i]
            answers.append(self.convert_ids_to_tokens(p))

        return answers

    def custom_standardization(self, input_data):
        lowercase = tf.strings.lower(input_data)
        return lowercase

    def get_vectorize_layer(self, texts, special_tokens=["mask"]):
        """Build Text vectorization layer

        Args:
          texts (list): List of string i.e input texts
          vocab_size (int): vocab size
          max_seq (int): Maximum sequence lenght.
          special_tokens (list, optional): List of special tokens. Defaults to ['[MASK]'].

        Returns:
            layers.Layer: Return TextVectorization Keras Layer
        """
        vectorize_layer = TextVectorization(
            max_tokens=self.config.VOCAB_SIZE,
            output_mode="int",
            ngrams=None,
            standardize="lower_and_strip_punctuation",
            output_sequence_length=self.config.MAX_LEN,
        )
        vectorize_layer.adapt(texts)

        # Insert mask token in vocabulary
        vocab = vectorize_layer.get_vocabulary()
        vocab = vocab[2: self.config.VOCAB_SIZE - len(special_tokens)] + ["mask"]
        vectorize_layer.set_vocabulary(vocab)
        return vectorize_layer
    