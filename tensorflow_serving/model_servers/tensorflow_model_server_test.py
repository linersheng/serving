# Copyright 2016 Google Inc. All Rights Reserved.
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

#!/usr/bin/env python2.7

"""Tests for tensorflow_model_server."""

import atexit
import os
import shlex
import socket
import subprocess
import sys
import time

# This is a placeholder for a Google-internal import.

from grpc.beta import implementations
from grpc.beta import interfaces as beta_interfaces
from grpc.framework.interfaces.face import face
import tensorflow as tf

from tensorflow.core.framework import types_pb2
from tensorflow.python.platform import flags
from tensorflow_serving.apis import predict_pb2
from tensorflow_serving.apis import prediction_service_pb2

FLAGS = flags.FLAGS


def PickUnusedPort():
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  s.bind(('localhost', 0))
  _, port = s.getsockname()
  s.close()
  return port


class TensorflowModelServerTest(tf.test.TestCase):
  """This class defines integration test cases for tensorflow_model_server."""

  def __TestSrcDirPath(self, relative_path):
    return os.path.join(os.environ['TEST_SRCDIR'],
                        'tf_serving/tensorflow_serving', relative_path)

  def setUp(self):
    """Sets up integration test parameters."""
    self.binary_dir = self.__TestSrcDirPath('model_servers')
    self.testdata_dir = self.__TestSrcDirPath('servables/tensorflow/testdata')
    self.server_proc = None

  def TerminateProcs(self):
    """Terminate all processes."""
    print 'Terminating all processes...'
    if self.server_proc is not None:
      self.server_proc.terminate()

  def RunServer(self, port, model_name, model_path, use_saved_model,
                enable_batching):
    """Run tensorflow_model_server using test config."""
    print 'Starting test server...'
    command = os.path.join(self.binary_dir, 'tensorflow_model_server')
    command += ' --port=' + str(port)
    command += ' --model_name=' + model_name
    command += ' --model_base_path=' + model_path
    command += ' --use_saved_model=' + str(use_saved_model).lower()
    command += ' --enable_batching=' + str(enable_batching).lower()
    command += ' --alsologtostderr'
    print command
    self.server_proc = subprocess.Popen(shlex.split(command))
    print 'Server started'
    return 'localhost:' + str(port)

  def VerifyPredictRequest(self,
                           model_server_address,
                           specify_output=True):
    """Send PredictionService.Predict request and verify output."""
    print 'Sending Predict request...'
    # Prepare request
    request = predict_pb2.PredictRequest()
    request.model_spec.name = 'default'
    request.inputs['x'].dtype = types_pb2.DT_FLOAT
    request.inputs['x'].float_val.append(2.0)
    dim = request.inputs['x'].tensor_shape.dim.add()
    dim.size = 1

    if specify_output:
      request.output_filter.append('y')
    # Send request
    host, port = model_server_address.split(':')
    channel = implementations.insecure_channel(host, int(port))
    stub = prediction_service_pb2.beta_create_PredictionService_stub(channel)
    result = stub.Predict(request, 5.0)  # 5 secs timeout
    # Verify response
    self.assertTrue('y' in result.outputs)
    self.assertIs(types_pb2.DT_FLOAT, result.outputs['y'].dtype)
    self.assertEquals(1, len(result.outputs['y'].float_val))
    self.assertEquals(3.0, result.outputs['y'].float_val[0])

  def _GetSavedModelBundlePath(self):
    """Returns a path to a model in SavedModel format."""
    return os.path.join(os.environ['TEST_SRCDIR'], 'tf_serving/external/org_tensorflow/tensorflow/',
                        'cc/saved_model/testdata/half_plus_two')

  def _GetSessionBundlePath(self):
    """Returns a path to a model in SessionBundle format."""
    return os.path.join(self.testdata_dir, 'half_plus_two')

  def _TestPredict(self, model_path, use_saved_model, enable_batching):
    """Helper method to test prediction.

    Args:
      model_path:      Path to the model on disk.
      use_saved_model: Whether the model server should use SavedModel.
      enable_batching: Whether model server should use BatchingSession
    """
    atexit.register(self.TerminateProcs)
    model_server_address = self.RunServer(PickUnusedPort(), 'default',
                                          model_path, use_saved_model,
                                          enable_batching)
    time.sleep(5)
    self.VerifyPredictRequest(model_server_address)
    self.VerifyPredictRequest(model_server_address, specify_output=False)

  def testPredictSessionBundle(self):
    """Test PredictionService.Predict implementation with SessionBundle."""
    self._TestPredict(self._GetSessionBundlePath(), use_saved_model=False,
                      enable_batching=False)

  def testPredictBatchingSessionBundle(self):
    """Test PredictionService.Predict implementation with SessionBundle."""
    self._TestPredict(self._GetSessionBundlePath(), use_saved_model=False,
                      enable_batching=True)

  def testPredictSavedModel(self):
    """Test PredictionService.Predict implementation with SavedModel."""
    self._TestPredict(self._GetSavedModelBundlePath(), use_saved_model=True,
                      enable_batching=False)

  def testPredictUpconvertedSavedModel(self):
    """Test PredictionService.Predict implementation.

    Using a SessionBundle converted to a SavedModel.
    """
    self._TestPredict(self._GetSessionBundlePath(), use_saved_model=True,
                      enable_batching=False)

  def _TestBadModel(self, use_saved_model):
    """Helper method to test against a bad model export."""
    atexit.register(self.TerminateProcs)
    # Both SessionBundle and SavedModel use the same bad model path, but in the
    # case of SavedModel, the export will get up-converted to a SavedModel.
    model_server_address = self.RunServer(
        PickUnusedPort(), 'default',
        os.path.join(self.testdata_dir, 'bad_half_plus_two'), use_saved_model,
        enable_batching=False)
    time.sleep(5)
    with self.assertRaises(face.AbortionError) as error:
      self.VerifyPredictRequest(model_server_address)
    self.assertIs(beta_interfaces.StatusCode.FAILED_PRECONDITION,
                  error.exception.code)

  def _TestBadModelUpconvertedSavedModel(self):
    """Test Predict against a bad upconverted SavedModel model export."""
    self._TestBadModel(use_saved_model=True)

  def _TestBadModelSessionBundle(self):
    """Test Predict against a bad SessionBundle model export."""
    self._TestBadModel(use_saved_model=False)


if __name__ == '__main__':
  tf.test.main()
