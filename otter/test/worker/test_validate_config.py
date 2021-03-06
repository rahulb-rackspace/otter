"""
Tests for `worker.validate_config.py`
"""

import base64
import json

import mock

from twisted.internet import defer
from twisted.trial.unittest import SynchronousTestCase

from otter.test.utils import CheckFailure, mock_log, mock_treq, patch
from otter.util.config import set_config_data
from otter.util.http import RequestError, headers

from otter.worker.validate_config import (
    InactiveImage,
    InvalidBase64Encoding,
    InvalidFileContentSize,
    InvalidLaunchConfiguration,
    InvalidMaxPersonality,
    InvalidPersonality,
    UnknownFlavor,
    UnknownImage,
    get_heat_endpoint,
    get_servers_endpoint,
    shorten,
    validate_flavor,
    validate_image,
    validate_launch_server_config,
    validate_launch_stack_config,
    validate_personality)


class ValidateLaunchServerConfigTests(SynchronousTestCase):
    """
    Tests for `validate_launch_server_config`
    """

    def setUp(self):
        """
        Mock supervisor and other validate_* methods
        """
        self.log = mock_log()
        self.launch_config = {
            'server': {
                'imageRef': 'imagegood',
                'flavorRef': 'flavoreor',
                'personality': 'some_personality'
            }
        }
        self.func_suffixes = ['image', 'flavor', 'personality']
        self.properties = ['imageRef', 'flavorRef', 'personality']
        for func_suffix in self.func_suffixes:
            setattr(self, 'validate_{}'.format(func_suffix), patch(
                self,
                'otter.worker.validate_config.validate_{}'.format(func_suffix),
                return_value=defer.succeed(None)))
        self.get_service_endpoint = patch(
            self, 'otter.worker.validate_config.get_service_endpoint',
            return_value='service')

    def test_valid(self):
        """
        `validate_launch_server_config` succeeds when all the validate_* are
        called and succeeds.
        """
        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        self.successResultOf(d)

        for suffix, prop in zip(self.func_suffixes, self.properties):
            func = getattr(self, 'validate_{}'.format(suffix))
            func.assert_called_once_with(self.log, 'token', 'service',
                                         self.launch_config['server'][prop])

    def test_invalid_image(self):
        """
        Invalid image causes InvalidLaunchConfiguration
        """
        self.validate_image.return_value = defer.fail(UnknownImage('meh'))
        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        f = self.failureResultOf(d, InvalidLaunchConfiguration)
        self.assertEqual(f.value.message,
                         ('Following problems with launch configuration:\n' +
                          'Invalid imageRef "meh" in launchConfiguration'))

    def test_inactive_image(self):
        """
        Image that is not in 'ACTIVE' state causes InvalidLaunchConfiguration
        """
        self.validate_image.return_value = defer.fail(InactiveImage('meh'))
        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        f = self.failureResultOf(d, InvalidLaunchConfiguration)
        self.assertEqual(f.value.message,
                         ('Following problems with launch configuration:\n' +
                          'Inactive imageRef "meh" in launchConfiguration'))

    def test_empty_image(self):
        """
        An empty string in the image param means that Nova will use
        boot-from-volume.  The empty string still validates.
        """
        self.launch_config['server']['imageRef'] = ""

        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        self.successResultOf(d)

        self.assertFalse(self.validate_image.called)

    def test_null_image(self):
        """
        None in the image param means that Nova will use boot-from-volume.
        The ``None`` ``imageRef`` still validates.
        """
        self.launch_config['server']['imageRef'] = None

        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        self.successResultOf(d)

        self.assertFalse(self.validate_image.called)

    def test_no_image(self):
        """
        Not providing an ``imageRef`` param means that Nova will use
        boot-from-volume.  The server args still validate.
        """
        self.launch_config['server'].pop('imageRef')

        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        self.successResultOf(d)

        self.assertFalse(self.validate_image.called)

    def test_invalid_flavor(self):
        """
        Invalid flavor causes InvalidLaunchConfiguration
        """
        self.validate_flavor.return_value = defer.fail(
            InvalidLaunchConfiguration(':('))
        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        f = self.failureResultOf(d, InvalidLaunchConfiguration)
        self.assertEqual(f.value.message,
                         'Following problems with launch configuration:\n:(')

    def test_invalid_image_and_flavor(self):
        """
        InvalidLaunchConfiguration is raised if both image and flavor are
        invalid.
        """
        self.validate_image.return_value = defer.fail(
            InvalidLaunchConfiguration('image problem'))
        self.validate_flavor.return_value = defer.fail(
            InvalidLaunchConfiguration('flavor problem'))
        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        f = self.failureResultOf(d, InvalidLaunchConfiguration)
        self.assertEqual(f.value.message,
                         ('Following problems with launch configuration:\n' +
                          'image problem\nflavor problem'))

    def test_invalid_personality(self):
        """
        Invalid personality causes InvalidLaunchConfiguration
        """
        self.validate_personality.return_value = defer.fail(
            InvalidPersonality(':('))
        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        f = self.failureResultOf(d, InvalidLaunchConfiguration)
        self.assertEqual(f.value.message,
                         'Following problems with launch configuration:\n:(')

    def test_other_error_raised(self):
        """
        `InvalidLaunchConfiguration` is raised even if any of the internal
        validate_* functions raise some other error
        """
        self.validate_image.return_value = defer.fail(ValueError(':('))
        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        f = self.failureResultOf(d, InvalidLaunchConfiguration)
        self.assertEqual(
            f.value.message,
            ('Following problems with launch configuration:\n'
             'Invalid imageRef "imagegood" in launchConfiguration'))

    def test_validation_error_logged(self):
        """
        `InvalidLaunchConfiguration` is logged as msg
        """
        self.validate_image.return_value = defer.fail(
            InvalidLaunchConfiguration(':('))
        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        self.failureResultOf(d, InvalidLaunchConfiguration)
        self.log.msg.assert_called_once_with(
            'Invalid {prop_name} "{prop_value}" in launchConfiguration',
            prop_name='imageRef', prop_value='imagegood',
            reason=CheckFailure(InvalidLaunchConfiguration))

    def test_optional_property(self):
        """
        If any of the optional properties is not available, it does not
        validate those and continues validating others
        """
        # Note: flavorRef is actually mandatory. It is used only for testing
        # purposes.
        del self.launch_config['server']['flavorRef']
        d = validate_launch_server_config(self.log, 'dfw', 'catalog', 'token',
                                          self.launch_config)
        self.successResultOf(d)
        self.assertFalse(self.validate_flavor.called)


class ValidateLaunchStackConfigTests(SynchronousTestCase):
    """Tests for `validate_launch_stack_config`."""
    def setUp(self):
        self.log = mock_log()
        self.launch_config = {'stack': {'foo': 'bar'}}
        self.expected_args = {'foo': 'bar', 'stack_name': 'as_some_uuid'}

        self.get_heat_endpoint = patch(
            self, 'otter.worker.validate_config.get_heat_endpoint',
            return_value='https://service')

        self.uuid4 = patch(
            self,
            'uuid.uuid4',
            return_value='some_uuid')
        patch(self, 'otter.worker.validate_config.uuid4', new=self.uuid4)

    def patch_treq(self, the_mock):
        """Convenience method for patching the treq methods."""
        self.treq = patch(self, 'otter.worker.validate_config.treq',
                          new=the_mock)
        patch(self, 'otter.util.http.treq', new=self.treq)

    def test_valid(self):
        """Result is successful when stack preview returns success."""
        self.patch_treq(
            mock_treq(code=200, json_content={'stack': {}}, method='post'))

        d = validate_launch_stack_config(self.log, 'dfw', 'catalog', 'token',
                                         self.launch_config)

        self.successResultOf(d)
        self.treq.post.assert_called_with('https://service/stacks/preview',
                                          json.dumps(self.expected_args),
                                          headers=headers('token'),
                                          log=self.log)

    def test_invalid(self):
        """
        Certain 40x responses should be handled and result in
        InvalidLaunchConfiguration.
        """
        for code in [400, 404, 409]:
            json_content = {"explanation": "some explanation %s" % code}

            self.patch_treq(mock_treq(code=code,
                                      json_content=json_content,
                                      content=json.dumps(json_content),
                                      method='post'))

            d = validate_launch_stack_config(self.log, 'dfw', 'catalog',
                                             'token', self.launch_config)

            f = self.failureResultOf(d, InvalidLaunchConfiguration)
            self.assertEqual(f.value.message, json.dumps(json_content))


class ShortenTests(SynchronousTestCase):
    """
    Tests for `worker.shorten`
    """

    def test_shortens(self):
        """
        shortens the string to required length
        """
        self.assertEqual(shorten('manish is bad boy', 12), 'manish is...')

    def test_no_shorten(self):
        """
        Does not shorten when required length is greated
        """
        self.assertEqual(shorten('manish is bad boy', 32), 'manish is bad boy')

    def test_shorten_equal(self):
        """
        Does not shorten when required length is equal
        """
        s = 'manish is bad boy'
        self.assertEqual(shorten(s, len(s)), s)


class ValidateImageTests(SynchronousTestCase):
    """
    Tests for `validate_image`
    """

    def setUp(self):
        """
        Mock treq
        """
        self.log = mock_log()
        self.treq = patch(
            self,
            'otter.worker.validate_config.treq',
            new=mock_treq(code=200,
                          json_content={'image': {'status': 'ACTIVE'}},
                          method='get'))
        patch(self, 'otter.util.http.treq', new=self.treq)
        self.headers = {'content-type': ['application/json'],
                        'accept': ['application/json'],
                        'User-Agent': ['OtterScale/0.0']}

    def test_valid(self):
        """
        Succeeds if given image is valid
        """
        self.headers['x-auth-token'] = ['token']
        d = validate_image(self.log, 'token', 'endpoint', 'image_ref')
        self.successResultOf(d)
        self.treq.get.assert_called_with(
            'endpoint/images/image_ref', headers=self.headers, log=self.log)

    def test_inactive_image(self):
        """
        `InactiveImage` is raised if given image is inactive
        """
        self.treq.json_content.side_effect = (
            lambda r: defer.succeed({'image': {'status': 'INACTIVE'}}))
        d = validate_image(self.log, 'token', 'endpoint', 'image_ref')
        self.failureResultOf(d, InactiveImage)

    def test_unknown_image(self):
        """
        `UnknownImage` is raised if imageRef is unknown
        """
        self.treq.get.return_value = defer.succeed(mock.Mock(code=404))
        d = validate_image(self.log, 'token', 'endpoint', 'image_ref')
        self.failureResultOf(d, UnknownImage)

    def test_unexpected_http_status(self):
        """
        Errbacks if unexpected HTTP status is returned
        """
        self.treq.get.return_value = defer.succeed(mock.Mock(code=500))
        d = validate_image(self.log, 'token', 'endpoint', 'image_ref')
        self.failureResultOf(d)


class ValidateFlavorTests(SynchronousTestCase):
    """
    Tests for `validate_flavor`
    """

    def setUp(self):
        """
        Mock treq
        """
        self.log = mock_log()
        self.treq = patch(self, 'otter.worker.validate_config.treq',
                          new=mock_treq(code=200, method='get'))
        patch(self, 'otter.util.http.treq', new=self.treq)
        self.headers = {'content-type': ['application/json'],
                        'accept': ['application/json'],
                        'User-Agent': ['OtterScale/0.0']}

    def test_valid(self):
        """
        Succeeds if given flavor is valid
        """
        self.headers['x-auth-token'] = ['token']
        d = validate_flavor(self.log, 'token', 'endpoint', 'flavornum')
        self.successResultOf(d)
        self.treq.get.assert_called_once_with(
            'endpoint/flavors/flavornum', headers=self.headers, log=self.log)

    def test_unknown_flavor(self):
        """
        UnknownFlavor is raised if flavor is unknown
        """
        self.treq.get.return_value = defer.succeed(mock.Mock(code=404))
        d = validate_flavor(self.log, 'token', 'endpoint', 'flavornum')
        self.failureResultOf(d, UnknownFlavor)

    def test_unexpected_http_status(self):
        """
        Errbacks if unexpected HTTP status is returned
        """
        self.treq.get.return_value = defer.succeed(mock.Mock(code=500))
        d = validate_flavor(self.log, 'token', 'endpoint', 'flavor_some')
        self.failureResultOf(d)


class ValidatePersonalityTests(SynchronousTestCase):
    """
    Tests for `validate_personality`
    """

    def setUp(self):
        """
        Mock treq
        """
        self.log = mock_log()
        limits = {
            'limits': {
                'absolute': {'maxPersonality': 1, 'maxPersonalitySize': 35}}}
        self.treq = patch(
            self,
            'otter.worker.validate_config.treq',
            new=mock_treq(code=200, method='get', json_content=limits))
        patch(self, 'otter.util.http.treq', new=self.treq)
        self.personality = [
            {'path': '/etc/banner.txt',
             'contents': base64.b64encode('This is a test of base64 encoding')}
        ]
        self.headers = {'content-type': ['application/json'],
                        'accept': ['application/json'],
                        'User-Agent': ['OtterScale/0.0']}

    def test_valid(self):
        """
        Succeeds if given personality is valid
        """
        self.headers['x-auth-token'] = ['token']
        d = validate_personality(self.log, 'token', 'endpoint',
                                 self.personality)
        self.successResultOf(d)
        self.treq.get.assert_called_once_with(
            'endpoint/limits', headers=self.headers, log=self.log)

    def test_limit_failure_succeeds(self):
        """
        If getting /limits fails, then it logs and just validates base64
        encoding and succeed
        """
        self.treq.get.return_value = defer.succeed(mock.Mock(code=500))
        d = validate_personality(self.log, 'token', 'endpoint',
                                 self.personality)
        self.successResultOf(d)
        self.log.msg.assert_called_once_with(
            'Skipping personality size checks due to limits error',
            reason=CheckFailure(RequestError))

    def test_invalid_base64_encoding(self):
        """
        Fails when content is not base64 encoded
        """
        self.personality[0]['contents'] = 'bad encoding'
        d = validate_personality(self.log, 'token', 'endpoint',
                                 self.personality)
        f = self.failureResultOf(d, InvalidBase64Encoding)
        self.assertEqual(
            f.value.message,
            'Invalid base64 encoding for contents of path "/etc/banner.txt"')

    def test_invalid_base64_chars(self):
        """
        Fails when content is having non-base64 characters but is valid
        encoding.
        """
        self.personality[0]['contents'] = '()()'
        d = validate_personality(self.log, 'token', 'endpoint',
                                 self.personality)
        f = self.failureResultOf(d, InvalidBase64Encoding)
        self.assertEqual(
            f.value.message,
            'Invalid base64 encoding for contents of path "/etc/banner.txt"')

    def test_exceeds_max_personality(self):
        """
        Fails when number of files in personality exceeds max limit
        """
        self.personality.append(
            {'path': '/somepath', 'contents': self.personality[0]['contents']})
        d = validate_personality(self.log, 'token', 'endpoint',
                                 self.personality)
        f = self.failureResultOf(d, InvalidMaxPersonality)
        self.assertEqual(
            f.value.message,
            'Number of files "2" in personality exceeds maximum limit "1"')

    def test_exceeds_max_personality_size(self):
        """
        Fails when size of decoded content in personality exceeds max limit
        """
        self.personality[0]['contents'] = base64.b64encode('abc' * 30)
        d = validate_personality(self.log, 'token', 'endpoint',
                                 self.personality)
        f = self.failureResultOf(d, InvalidFileContentSize)
        self.assertEqual(
            f.value.message,
            'File "/etc/banner.txt" content\'s size exceeds maximum size "35"')


class GetServiceEndpointTests(SynchronousTestCase):
    """
    Tests for `get_servers_endpoint` and `get_heat_endpoint`.
    """

    def setUp(self):
        """
        Mock public_endpoint_url and set_config_data
        """
        set_config_data({'cloudServersOpenStack': 'cloud',
                         'cloudOrchestration': 'orch'})
        self.public_endpoint_url = patch(
            self,
            'otter.worker.validate_config.public_endpoint_url',
            return_value='http://service')

    def tearDown(self):
        """
        Reset config data
        """
        set_config_data({})

    def test_servers_works(self):
        """
        Calls `public_endpoint_url` with correct args.
        """
        endpoint = get_servers_endpoint('catalog', 'dfw')
        self.assertEqual(endpoint, 'http://service')
        self.public_endpoint_url.assert_called_once_with(
            'catalog', 'cloud', 'dfw')

    def test_heat_works(self):
        """
        Calls `public_endpoint_url` with correct args.
        """
        endpoint = get_heat_endpoint('catalog', 'dfw')
        self.assertEqual(endpoint, 'http://service')
        self.public_endpoint_url.assert_called_once_with(
            'catalog', 'orch', 'dfw')
