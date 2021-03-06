# Copyright 2016 The TensorFlow Authors. All Rights Reserved.
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
# ==============================================================================

# ======================================================================== #
# 
# Copyright (c) 2017 - 2018 scVAE authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# 
# ======================================================================== #

"""The Pareto distribution class (Type 1)."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from tensorflow import where
from tensorflow import ones_like
from tensorflow.python.ops.distributions import distribution
from tensorflow.python.ops.distributions import util as distribution_util
from tensorflow.contrib.framework.python.framework import tensor_util as contrib_tensor_util
from tensorflow.python.framework import constant_op
from tensorflow.python.framework import dtypes
from tensorflow.python.framework import ops
from tensorflow.python.framework import tensor_shape
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import check_ops
from tensorflow.python.ops import control_flow_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import clip_ops



class Pareto(distribution.Distribution):
  """pareto distribution (Type 1).

  The pareto distribution is (Type 1) parameterized by `sigma`, the scale parameter, and by 'alpha', the shape parameter.

  The pmf of this distribution is:

  ```
  pmf(x) = (alpha * sigma^alpha)/(x^{alpha+1})    , for x >= sigma
  pmf(x) = 0                                      , for x < sigma
  ```

  """

  def __init__(self, 
               alpha,
               validate_args=False,
               allow_nan_stats=True,
               name="Pareto"):
    """Construct pareto distributions (Type 1).

    Args:
      alpha: Floating point tensor, the shape parameter of the distribution(s). `alpha` must be positive and non-zero.
      validate_args: `Boolean`, default `False`.  Whether to assert that
        `p > 0` as well as inputs to pmf computations are non-negative
        integers. If validate_args is `False`, then `pmf` computations might
        return `NaN`, but can be evaluated at any real value.
      allow_nan_stats: `Boolean`, default `True`.  If `False`, raise an
        exception if a statistic (e.g. mean/mode/etc...) is undefined for any
        batch member.  If `True`, batch members with valid parameters leading to
        undefined statistics will return NaN for this statistic.
      name: A name for this distribution.
    """
    parameters = locals()
    parameters.pop("self")
    with ops.name_scope(name, values=[alpha]) as ns:
      with ops.control_dependencies([check_ops.assert_positive(alpha)] if
                                    validate_args else []):
        self._alpha = array_ops.identity(alpha, name="p")
    super(Pareto, self).__init__(
        dtype=self._alpha.dtype,
        is_continuous=True,
        is_reparameterized=False,
        validate_args=validate_args,
        allow_nan_stats=allow_nan_stats,
        parameters=parameters,
        graph_parents=[self._alpha],
        name=ns)

  @property
  def alpha(self):
    """Shape parameter."""
    return self._alpha

  def _batch_shape(self):
    return array_ops.shape(self.alpha)

  def _get_batch_shape(self):
    return self.alpha.get_shape()

  def _event_shape(self):
    return constant_op.constant([], dtype=dtypes.int32)

  def _get_event_shape(self):
    return tensor_shape.scalar()

  def _log_prob(self, x):
    x = self._assert_valid_sample(x, check_integer=False)
    #log_prob_zero = 10e-12 * ones_like(x)
    x = clip_ops.clip_by_value(x, 1e-8, x)
    return where(x >= 1.0, math_ops.log(self.alpha) - (self.alpha + 1)*math_ops.log(x), 0*x)

  # TODO:
  def _prob(self, x):
    # pmf(x) = (alpha * sigma^alpha)/(x^{alpha+1})    , for x >= sigma
    # pmf(x) = 0                                      , for x < sigma
    # where sigma = 1
    return math_ops.exp(self._log_prob(x))

  def _log_cdf(self, x):
    return math_ops.log(self.cdf(x))

  def _cdf(self, x):
    x = self._assert_valid_sample(x, check_integer=False)
    return 1 - math_ops.pow((1/x),self.alpha)

  def _mean(self):
    return (self.alpha) / (self.alpha - 1)

  # TODO:
  def _variance(self):
    return self.alpha / math_ops.square(1-self.alpha)

  # TODO:
  def _std(self):
    return math_ops.sqrt(self.variance())

  # TODO:
  def _mode(self):
    return where(self.alpha > 1, math_ops.floor(self.alpha*(-1)/(1-self.alpha)), 0.0)

  # TODO:
  def _assert_valid_sample(self, x, check_integer=False):
    if not self.validate_args: return x
    with ops.name_scope("check_x", values=[x]):
      dependencies = [check_ops.assert_non_negative(x)]
      if check_integer:
        dependencies += [distribution_util.assert_integer_form(
            x, message="x has non-integer components.")]
      return control_flow_ops.with_dependencies(dependencies, x)
