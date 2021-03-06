# -*- coding: utf-8 -*-
#
# Copyright 2018 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Module to create a client interacting with Google Cloud authentication.

An instantiated client is needed for interacting with any of the Google
APIs via the :class:`.AIOConnection`.

Only service account (JSON Web Tokens/JWT) authentication is currently
supported. To setup a service account, follow `Google's docs <https://
cloud.google.com/iam/docs/creating-managing-service-account-keys>`_.

To use:

.. code-block:: pycon

    >>> import asyncio
    >>> import google_janitor_gcp
    >>> loop = asyncio.get_event_loop()
    >>> keyfile = '/path/to/service_account_keyfile.json'
    >>> auth_client = google_janitor_gcp.GAuthClient(
    ...   keyfile=keyfile)
    >>> auth_clientcreds.token is None
    True
    >>> loop.run_until_complete(auth_client.refresh_token())
    >>> auth_client.creds.token
    'c0ffe3'

"""

import asyncio
import json
import logging
import urllib.parse

import aiohttp
from google.oauth2 import _client
from google.oauth2 import service_account

from gordon_janitor_gcp import exceptions


REQ_LOG_FMT = 'Request: "{method} {url}"'
RESP_LOG_FMT = 'Response: "{method} {url}" {status} {reason}'


__all__ = ('GAuthClient', )


class GAuthClient:
    """Async client to authenticate against Google Cloud APIs.

    Attributes:
        SCOPE_TMPL_URL (str): template URL for Google auth scopes.
        DEFAULT_SCOPE (str): default scope if not provided.
        JWT_GRANT_TYPE (str): grant type header value when
            requesting/refreshing an access token.

    Args:
        keyfile (str): path to service account (SA) keyfile.
        scopes (list): (optional) scopes with which to authorize the SA.
            Default is ``'cloud-platform'``.
        session (aiohttp.ClientSession): (optional) ``aiohttp`` HTTP
            session to use for sending requests.
        loop: (optional) asyncio event loop to use for HTTP requests.
            NOTE: if :obj:`session` is given, then :obj:`loop` will be
            ignored. Otherwise, :obj:`loop` will be used to create a
            session, if provided.

    """

    SCOPE_TMPL_URL = 'https://www.googleapis.com/auth/{scope}'
    DEFAULT_SCOPE = 'cloud-platform'
    JWT_GRANT_TYPE = 'urn:ietf:params:oauth:grant-type:jwt-bearer'

    def __init__(self, keyfile=None, scopes=None, session=None, loop=None):
        self._keydata = self._load_keyfile(keyfile)
        self.scopes = self._set_scopes(scopes)
        self.creds = self._load_credentials()
        self._session = self._set_session(session, loop)
        self.token = None
        self.expiry = None  # UTC time

    def _load_keyfile(self, keyfile):
        try:
            with open(keyfile, 'r') as f:
                return json.load(f)
        except FileNotFoundError as e:
            msg = f'Keyfile {keyfile} was not found.'
            logging.error(msg, exc_info=e)
            raise exceptions.GCPGordonJanitorError(msg)
        except json.JSONDecodeError as e:
            msg = f'Keyfile {keyfile} is not valid JSON.'
            logging.error(msg, exc_info=e)
            raise exceptions.GCPGordonJanitorError(msg)

    def _set_scopes(self, scopes):
        if not scopes:
            scopes = [self.DEFAULT_SCOPE]
        return [self.SCOPE_TMPL_URL.format(scope=s) for s in scopes]

    def _load_credentials(self):
        # TODO (lynn): FEATURE - load other credentials like app default
        return service_account.Credentials.from_service_account_info(
            self._keydata, scopes=self.scopes)

    def _set_session(self, session, loop):
        if session is not None:
            return session
        if not loop:
            loop = asyncio.get_event_loop()
        session = aiohttp.ClientSession(loop=loop)
        return session

    def _setup_token_request(self):
        url = self._keydata.get('token_uri')
        headers = {
            'Content-type': 'application/x-www-form-urlencoded',
        }
        body = {
            'assertion': self.creds._make_authorization_grant_assertion(),
            'grant_type': self.JWT_GRANT_TYPE,
        }
        body = urllib.parse.urlencode(body)
        return url, headers, bytes(body.encode('utf-8'))

    async def refresh_token(self):
        """Refresh oauth access token attached to this HTTP session.

        Raises:
            :exc:`.GCPAuthError`: if no token was found in the
                response.
            :exc:`.GCPHTTPError`: if any exception occurred.
        """
        url, headers, body = self._setup_token_request()

        logging.debug(REQ_LOG_FMT.format(method='POST', url=url))
        async with self._session.post(url, headers=headers, data=body) as resp:
            log_kw = {
                'method': 'POST',
                'url': url,
                'status': resp.status,
                'reason': resp.reason,
            }
            logging.debug(RESP_LOG_FMT.format(**log_kw))

            # avoid leaky abstractions and wrap http errors with our own
            try:
                resp.raise_for_status()
            except aiohttp.ClientResponseError as e:
                msg = f'Issue connecting to {resp.url.host}: {e}'
                logging.error(msg, exc_info=e)
                raise exceptions.GCPHTTPError(msg)

            response = await resp.json()
            try:
                self.token = response['access_token']
            except KeyError:
                msg = 'No access token in response.'
                logging.error(msg)
                raise exceptions.GCPAuthError(msg)

        self.expiry = _client._parse_expiry(response)
