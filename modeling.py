import tensorflow as tf
from tensorflow.contrib.layers import fully_connected, batch_norm
from tensorflow.python.ops.nn import relu
from tensorflow import sigmoid, identity
from tensorflow.contrib.distributions import Bernoulli, Normal, Poisson, Categorical, kl
from distributions import (
    ZeroInflatedPoisson, NegativeBinomial, ZeroInflatedNegativeBinomial, ZeroInflated, Categorized, Pareto
)

import os, shutil

import numpy

from numpy import inf

from time import time

eps = 1e-6

class VariationalAutoEncoder(object):
    def __init__(self, feature_size, latent_size, hidden_sizes,
        reconstruction_distribution = None, number_of_reconstruction_classes = None,
        use_batch_norm = True, use_count_sum = True, epsilon = 1e-6):
        
        # Setup
        
        super(VariationalAutoEncoder, self).__init__()
        
        self.feature_size = feature_size
        self.latent_size = latent_size
        self.hidden_sizes = hidden_sizes
        
        self.reconstruction_distribution_name = reconstruction_distribution
        self.reconstruction_distribution = distributions[reconstruction_distribution]
        
        self.k_max = number_of_reconstruction_classes
        
        self.use_batch_norm = use_batch_norm
        self.use_count_sum = use_count_sum
        
        self.epsilon = epsilon
        
        self.graph = tf.Graph()
        
        with self.graph.as_default():
        
            self.x = tf.placeholder(tf.float32, [None, self.feature_size], 'x') # counts
        
            if self.use_count_sum:
                self.n = tf.placeholder(tf.float32, [None, 1], 'N') # total counts sum
        
            self.learning_rate = tf.placeholder(tf.float32, [], 'learning_rate')
            self.warm_up_weight = tf.placeholder(tf.float32, [], 'warm_up_weight')
            
            self.is_training = tf.placeholder(tf.bool, [], 'phase')
            
            self.inference()
            self.loss()
            self.training()
            
            self.saver = tf.train.Saver()
            
            print("Trainable parameters:")
        
            trainable_parameters = tf.trainable_variables()
        
            width = max(map(len, [p.name for p in trainable_parameters]))
        
            for parameter in trainable_parameters:
                print("    {:{}}  {}".format(
                    parameter.name, width, parameter.get_shape()))
    
    @property
    def name(self):
        
        model_name = self.reconstruction_distribution_name.replace(" ", "_")
        
        # if self.k_max:
        #     model_name += "_c_" + str(self.k_max)
        
        if self.use_count_sum:
            model_name += "_sum"
        
        model_name += "_l_" + str(self.latent_size) + "_h_" + "_".join(map(str,self.hidden_sizes))
        
        if self.use_batch_norm:
            model_name += "_bn"
        
        # model_name += "_lr_{:.1g}".format(self.learning_rate)
        # model_name += "_b_" + str(self.batch_size)
        # model_name += "_wu_" + str(number_of_warm_up_epochs)
        
        # model_name += "_e_" + str(number_of_epochs)
        
        return model_name

    def inference(self):
        
        encoder = self.x
        
        with tf.variable_scope("ENCODER"):
            for i, hidden_size in enumerate(self.hidden_sizes):
                encoder = dense_layer(
                    inputs = encoder,
                    num_outputs = hidden_size,
                    activation_fn = relu,
                    use_batch_norm = self.use_batch_norm, 
                    is_training = self.is_training,
                    scope = '{:d}'.format(i + 1)
                )
        
        with tf.variable_scope("Z"):
            z_mu = dense_layer(
                inputs = encoder,
                num_outputs = self.latent_size,
                activation_fn = None,
                use_batch_norm = False,
                is_training = self.is_training,
                scope = 'MU')
            
            z_sigma = dense_layer(
                inputs = encoder,
                num_outputs = self.latent_size,
                activation_fn = lambda x: tf.exp(tf.clip_by_value(x, -3, 3)),
                use_batch_norm = False,
                is_training = self.is_training,
                scope = 'SIGMA')
            
            self.q_z_given_x = Normal(mu = z_mu, sigma = z_sigma)
            
            # Mean of z
            self.z_mean = self.q_z_given_x.mean()
        
            # Stochastic layer
            self.z = self.q_z_given_x.sample()
        
        # Decoder - Generative model, p(x|z)
        
        if self.use_count_sum:
            decoder = tf.concat([self.z, self.n], axis = 1, name = 'Z_N')
        else:
            decoder = self.z
        
        with tf.variable_scope("DECODER"):
            for i, hidden_size in enumerate(reversed(self.hidden_sizes)):
                decoder = dense_layer(
                    inputs = decoder,
                    num_outputs = hidden_size,
                    activation_fn = relu,
                    use_batch_norm = self.use_batch_norm,
                    is_training = self.is_training,
                    scope = '{:d}'.format(len(self.hidden_sizes) - i)
                )

        # Reconstruction distribution parameterisation
        
        with tf.variable_scope("X_TILDE"):
            
            x_theta = {}
        
            for parameter in self.reconstruction_distribution["parameters"]:
                
                parameter_activation_function = \
                    self.reconstruction_distribution["parameters"]\
                    [parameter]["activation function"]
                p_min, p_max = \
                    self.reconstruction_distribution["parameters"]\
                    [parameter]["support"]
                
                x_theta[parameter] = dense_layer(
                    inputs = decoder,
                    num_outputs = self.feature_size,
                    activation_fn = lambda x: tf.clip_by_value(
                        parameter_activation_function(x),
                        p_min + self.epsilon,
                        p_max - self.epsilon
                    ),
                    is_training = self.is_training,
                    scope = parameter.upper()
                )
        
            self.p_x_given_z = self.reconstruction_distribution["class"](x_theta)
        
            if self.k_max:
                
                x_logits = dense_layer(
                    inputs = decoder,
                    num_outputs = self.feature_size * self.k_max,
                    activation_fn = None,
                    is_training = self.is_training,
                    scope = "P_K"
                )
                
                x_logits = tf.reshape(x_logits,
                    [-1, self.feature_size, self.k_max])
                
                self.p_x_given_z = Categorized(
                    dist = self.p_x_given_z,
                    cat = Categorical(logits = x_logits)
                )
            
            self.x_tilde_mean = self.p_x_given_z.mean()
        
        # Add histogram summaries for the trainable parameters
        parameter_summary_list = []
        for parameter in tf.trainable_variables():
            parameter_summary = tf.summary.histogram(parameter.name, parameter)
            parameter_summary_list.append(parameter_summary)
        self.parameter_summary = tf.summary.merge(parameter_summary_list)
    
    def loss(self):
        
        # Recognition prior
        p_z_mu = tf.constant(0.0, dtype = tf.float32)
        p_z_sigma = tf.constant(1.0, dtype = tf.float32)
        p_z = Normal(p_z_mu, p_z_sigma)
        
        # Loss
        
        ## Reconstruction error
        log_p_x_given_z = tf.reduce_mean(
            tf.reduce_sum(self.p_x_given_z.log_prob(self.x), axis = 1),
            name = 'reconstruction_error'
        )
        tf.add_to_collection('losses', log_p_x_given_z)
        self.ENRE = log_p_x_given_z
        
        ## Regularisation
        KL_qp = tf.reduce_mean(
            tf.reduce_sum(kl(self.q_z_given_x, p_z), axis = 1),
            name = "kl_divergence"
        )
        tf.add_to_collection('losses', KL_qp)
        self.KL = KL_qp
        
        # Averaging over samples.
        self.lower_bound = tf.subtract(log_p_x_given_z, KL_qp, name = 'lower_bound')
        tf.add_to_collection('losses', self.lower_bound)
        self.ELBO = self.lower_bound
        
        # Add scalar summaries for the losses
        # for l in tf.get_collection('losses'):
        #     tf.summary.scalar(l.op.name, l)
    
    def training(self):
        
        # Create the gradient descent optimiser with the given learning rate.
        def setupTraining():
            
            # Optimizer and training objective of negative loss
            optimiser = tf.train.AdamOptimizer(self.learning_rate)
            
            # Create a variable to track the global step.
            self.global_step = tf.Variable(0, name = 'global_step',
                trainable = False)
            
            # Use the optimiser to apply the gradients that minimize the loss
            # (and also increment the global step counter) as a single training
            # step.
            self.train_op = optimiser.minimize(
                -self.lower_bound,
                global_step = self.global_step
            )
        
        # Make sure that the updates of the moving_averages in batch_norm
        # layers are performed before the train_step.
        
        update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
        
        if update_ops:
            updates = tf.group(*update_ops)
            with tf.control_dependencies([updates]):
                setupTraining()
        else:
            setupTraining()

    def train(self, x_train, x_valid, number_of_epochs = 100, batch_size = 100,
        learning_rate = 1e-3, log_directory = None, reset_training = False):
        
        # Logging
        
        if reset_training and os.path.exists(log_directory):
            shutil.rmtree(log_directory)
        
        checkpoint_file = os.path.join(log_directory, 'model.ckpt')
        
        # Extra setup
        
        if self.use_count_sum:
            n_train = x_train.counts.sum(axis = 1).reshape(-1, 1)
            n_valid = x_valid.counts.sum(axis = 1).reshape(-1, 1)
        
        M_train = x_train.number_of_examples
        M_valid = x_valid.number_of_examples
        
        steps_per_epoch = numpy.ceil(M_train / batch_size)
        
        output_at_step = numpy.round(numpy.linspace(0, steps_per_epoch, 11))
        
        with tf.Session(graph = self.graph) as session:
            
            parameter_summary_writer = tf.summary.FileWriter(log_directory)
            training_summary_writer = tf.summary.FileWriter(
                os.path.join(log_directory, "training"))
            validation_summary_writer = tf.summary.FileWriter(
                os.path.join(log_directory, "validation"))
            
            # Initialisation
            
            checkpoint = tf.train.get_checkpoint_state(log_directory)
            
            if checkpoint:
                self.saver.restore(session, checkpoint.model_checkpoint_path)
                epoch_start = int(os.path.split(
                    checkpoint.model_checkpoint_path)[-1].split('-')[-1])
            else:
                session.run(tf.global_variables_initializer())
                epoch_start = 0
                parameter_summary_writer.add_graph(session.graph)
            
            # Training loop
            
            for epoch in range(epoch_start, number_of_epochs):
                
                epoch_time_start = time()
                epoch_steps = 0
                
                shuffled_indices = numpy.random.permutation(M_train)
                
                for i in range(0, M_train, batch_size):
                    
                    # Internal setup
                    
                    step_time_start = time()
                    
                    step = session.run(self.global_step)
                    
                    # Prepare batch
                    
                    batch_indices = shuffled_indices[i:(i + batch_size)]
                    
                    feed_dict_batch = {
                        self.x: x_train.counts[batch_indices],
                        self.is_training: True,
                        self.learning_rate: learning_rate
                    }
                    
                    if self.use_count_sum:
                        feed_dict_batch[self.n] = n_train[batch_indices]
                    
                    # Run the stochastic batch training operation
                    _, batch_loss = session.run(
                        [self.train_op, self.lower_bound],
                        feed_dict = feed_dict_batch
                    )

                    # Compute step duration
                    step_duration = time() - step_time_start
                    
                    # Print evaluation and output summaries
                    if (step + 1 - steps_per_epoch * epoch) in output_at_step:

                        print('Step {:d} ({:.3g} s): {:.5g}'.format(
                            int(step + 1), step_duration, batch_loss))
                    
                    epoch_steps += 1
                
                print()
                
                epoch_duration = time() - epoch_time_start
                
                print("Epoch {} ({:.3g} s):".format(epoch + 1, epoch_duration))
                
                # Saving model parameters
                print('    Saving model.')
                saving_time_start = time()
                self.saver.save(session, checkpoint_file,
                    global_step = epoch + 1)
                saving_duration = time() - saving_time_start
                print('    Model saved ({:.3g} s).'.format(saving_duration))
                
                # Export parameter summaries
                parameter_summary_string = session.run(self.parameter_summary)
                parameter_summary_writer.add_summary(
                    parameter_summary_string, global_step = epoch + 1)
                parameter_summary_writer.flush()
                
                # Evaluation
                print('    Evaluating model.')
                
                ## Training
                
                evaluating_time_start = time()
                
                ELBO_train = 0
                KL_train = 0
                ENRE_train = 0
                
                for i in range(0, M_train, batch_size):
                    subset = slice(i, (i + batch_size))
                    batch = x_train.counts[subset]
                    feed_dict_batch = {self.x: batch, self.is_training: False}
                    if self.use_count_sum:
                        feed_dict_batch[self.n] = n_train[subset]
                    ELBO_i, KL_i, ENRE_i = session.run(
                        [self.ELBO, self.KL, self.ENRE],
                        feed_dict = feed_dict_batch
                    )
                    ELBO_train += ELBO_i
                    KL_train += KL_i
                    ENRE_train += ENRE_i
                
                ELBO_train /= M_train / batch_size
                KL_train /= M_train / batch_size
                ENRE_train /= M_train / batch_size
                
                evaluating_duration = time() - evaluating_time_start
                
                summary = tf.Summary()
                summary.value.add(tag="lower_bound", simple_value = ELBO_train)
                summary.value.add(tag="reconstruction_error",
                    simple_value = ENRE_train)
                summary.value.add(tag="kl_divergence", simple_value = KL_train)
                training_summary_writer.add_summary(summary,
                    global_step = epoch + 1)
                training_summary_writer.flush()
                
                print("    Training set ({:.3g} s): ".format(
                    evaluating_duration) + \
                    "ELBO: {:.5g}, ENRE: {:.5g}, KL: {:.5g}.".format(
                    ELBO_train, ENRE_train, KL_train))
                
                ## Validation
                
                evaluating_time_start = time()
                
                ELBO_valid = 0
                KL_valid = 0
                ENRE_valid = 0
                
                for i in range(0, M_valid, batch_size):
                    subset = slice(i, (i + batch_size))
                    batch = x_valid.counts[subset]
                    feed_dict_batch = {self.x: batch, self.is_training: False}
                    if self.use_count_sum:
                        feed_dict_batch[self.n] = n_valid[subset]
                    ELBO_i, KL_i, ENRE_i = session.run(
                        [self.ELBO, self.KL, self.ENRE],
                        feed_dict = feed_dict_batch
                    )
                    ELBO_valid += ELBO_i
                    KL_valid += KL_i
                    ENRE_valid += ENRE_i
                
                ELBO_valid /= M_valid / batch_size
                KL_valid /= M_valid / batch_size
                ENRE_valid /= M_valid / batch_size
                
                summary = tf.Summary()
                summary.value.add(tag="lower_bound", simple_value = ELBO_valid)
                summary.value.add(tag="reconstruction_error",
                    simple_value = ENRE_valid)
                summary.value.add(tag="kl_divergence", simple_value = KL_valid)
                validation_summary_writer.add_summary(summary,
                    global_step = epoch + 1)
                validation_summary_writer.flush()
                
                evaluating_duration = time() - evaluating_time_start
                print("    Validation set ({:.3g} s): ".format(
                    evaluating_duration) + \
                    "ELBO: {:.5g}, ENRE: {:.5g}, KL: {:.5g}.".format(
                    ELBO_valid, ENRE_valid, KL_valid))
               
                print()
    
    def evaluate(self, test_set, batch_size = 100, log_directory = None):
        
        checkpoint = tf.train.get_checkpoint_state(log_directory)
        
        if self.use_count_sum:
            n_test = test_set.counts.sum(axis = 1).reshape(-1, 1)
                
        with tf.Session(graph = self.graph) as session:
        
            if checkpoint and checkpoint.model_checkpoint_path:
                self.saver.restore(session, checkpoint.model_checkpoint_path)
        
            lower_bound_test = 0
            recon_mean_test = numpy.empty([test_set.number_of_examples, test_set.number_of_features])
            z_mu_test = numpy.empty([test_set.number_of_examples, self.latent_size])
            for i in range(0, test_set.number_of_examples, batch_size):
                subset = slice(i, (i + batch_size))
                batch = test_set.counts[subset]
                feed_dict_batch = {self.x: batch, self.is_training: False}
                if self.use_count_sum:
                    feed_dict_batch[self.n] = n_test[subset]
                lower_bound_batch, recon_mean_batch, z_mu_batch = session.run([self.lower_bound, self.x_tilde_mean, self.z_mean], feed_dict = feed_dict_batch)
                lower_bound_test += lower_bound_batch
                recon_mean_test[subset] = recon_mean_batch
                z_mu_test[subset] = z_mu_batch
            lower_bound_test /= test_set.number_of_examples / batch_size

            metrics_test = {
                "LL_test": lower_bound_test
            }
        
            print(metrics_test)
        
            return recon_mean_test, z_mu_test, metrics_test

distributions = {
    "bernoulli": {
        "parameters": {
            "p": {
                "support": [0, 1],
                "activation function": sigmoid
            }
        },
        "class": lambda theta: Bernoulli(
            p = theta["p"]
        )
    },
    
    "gauss": {
        "parameters": {
            "mu": {
                "support": [-inf, inf],
                "activation function": identity
            },
            "log_sigma": {
                "support": [-10, 10],
                "activation function": identity
            }
        },
        "class": lambda theta: Normal(
            mu = theta["mu"],
            sigma = tf.exp(theta["log_sigma"])
        )
    },
    
    "poisson": {
        "parameters": {
            "log_lambda": {
                "support": [-10, 10],
                "activation function": identity
            }
        },
        "class": lambda theta: Poisson(
            lam = tf.exp(theta["log_lambda"])
        )
    },

    "pareto": {
        "parameters": {
            "log_sigma": {
                "support": [-10, 10],
                "activation function": identity
            },
            "log_alpha": {
                "support": [-10, 10],
                "activation function": identity
            }
        },
        "class": lambda theta: Pareto(
            sigma = tf.exp(theta["log_sigma"]),
            alpha = tf.exp(theta["log_alpha"])
        )
    },
    
    "zero-inflated poisson": {
        "parameters": {
            "pi": {
                "support": [0, 1],
                "activation function": sigmoid
            },
            "log_lambda": {
                "support": [-10, 10],
                "activation function": identity
            }
        },
        "class": lambda theta: ZeroInflated(
            Poisson(
                lam = tf.exp(theta["log_lambda"])
            ),
            pi = theta["pi"]
        )
    },
    
    "negative binomial": {
        "parameters": {
            "p": {
                "support": [0, 1],
                "activation function": sigmoid
            },
            "log_r": {
                "support": [-10, 10],
                "activation function": identity
            }
        },
        "class": lambda theta: NegativeBinomial(
            p = theta["p"],
            r = tf.exp(theta["log_r"])
        )
    },
    
    "zero-inflated negative binomial": {
        "parameters": {
            "pi": {
                "support": [0, 1],
                "activation function": sigmoid
            },
            "p": {
                "support": [0, 1],
                "activation function": sigmoid
            },
            "log_r": {
                "support": [-10, 10],
                "activation function": identity
            }
        },
        "class": lambda theta: ZeroInflated(
            NegativeBinomial(
                p = theta["p"],
                r = tf.exp(theta["log_r"])
            ),
            pi = theta["pi"]
        )
    }
}


# Wrapper layer for inserting batch normalization in between linear and nonlinear activation layers.
def dense_layer(inputs, num_outputs, is_training, scope, activation_fn = None,
    use_batch_norm = False, decay = 0.999, center = True, scale = False):
    
    with tf.variable_scope(scope):
        outputs = fully_connected(inputs, num_outputs = num_outputs, activation_fn = None, scope = 'DENSE')
        if use_batch_norm:
            outputs = batch_norm(outputs, center = center, scale = scale, is_training = is_training, scope = 'BATCH_NORM')
        if activation_fn is not None:
            outputs = activation_fn(outputs)
    
    return outputs
