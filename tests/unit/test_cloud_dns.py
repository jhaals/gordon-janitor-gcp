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

import asyncio
import logging

import attr
import pytest
from aioresponses import aioresponses

from gordon_janitor_gcp import cloud_dns


def test_create_gcp_rrset():
    """Create valid GCPResourceRecordSet instances."""
    data = {
        'name': 'test',
        'type': 'A',
        'rrdatas': ['10.1.2.3'],
        'ttl': 500
    }
    rrset = cloud_dns.GCPResourceRecordSet(**data)
    assert data == attr.asdict(rrset)

    # default TTL when not provided
    data.pop('ttl')
    rrset = cloud_dns.GCPResourceRecordSet(**data)
    data['ttl'] = 300
    assert data == attr.asdict(rrset)

    # Raise when required params are missing
    missing_params = {
        'name': 'test'
    }
    with pytest.raises(TypeError):
        cloud_dns.GCPResourceRecordSet(**missing_params)


args = 'scopes,provide_loop'
params = [
    [['not-a-real-scope'], True],
    [['not-a-real-scope'], False],
    [None, True],
    [None, False],
]


@pytest.mark.parametrize(args, params)
def test_dns_client_default(scopes, provide_loop, fake_keyfile,
                            mock_credentials, event_loop):
    loop = None
    if provide_loop:
        loop = event_loop

    client = cloud_dns.AIOGoogleDNSClient(
        'a-project', fake_keyfile, scopes, loop=loop)

    assert 'a-project' == client.project
    if not provide_loop:
        loop = asyncio.get_event_loop()
    assert loop == client._loop
    if not scopes:
        scopes = ['cloud-platform']
    exp_scopes = [f'https://www.googleapis.com/auth/{s}' for s in scopes]

    assert exp_scopes == client.scopes

    client._session.close()


@pytest.fixture
def fake_response_data():
    return {
        'rrsets': [
            {
                'name': 'a-test.example.net.',
                'type': 'A',
                'ttl': 300,
                'rrdatas': [
                    '10.1.2.3',
                ]
            }, {
                'name': 'b-test.example.net.',
                'type': 'CNAME',
                'ttl': 600,
                'rrdatas': [
                    'a-test.example.net.',
                ]
            }, {
                'name': 'c-test.example.net.',
                'type': 'TXT',
                'ttl': 300,
                'rrdatas': [
                    '"OHAI"',
                    '"OYE"',
                ]
            }
        ]
    }


@pytest.fixture
def client(fake_keyfile, mock_credentials):
    client = cloud_dns.AIOGoogleDNSClient('a-project', keyfile=fake_keyfile)
    yield client
    # test teardown
    client._session.close()


@pytest.mark.asyncio
async def test_get_records_for_zone(fake_response_data, client, caplog,
                                    mock_credentials, monkeypatch):
    caplog.set_level(logging.DEBUG)
    mock_get_json_called = 0

    async def mock_get_json(*args, **kwargs):
        nonlocal mock_get_json_called
        data = fake_response_data.copy()
        if not mock_get_json_called:
            data['nextPageToken'] = 1
        mock_get_json_called += 1
        return data

    monkeypatch.setattr(client, 'get_json', mock_get_json)

    url = f'{client._base_url}/managedZones/a-zone/rrsets'
    with aioresponses() as mocked:
        mocked.get(url, status=200)
        # paginated requests
        mocked.get(url, status=200)
        records = await client.get_records_for_zone('a-zone')

        assert all(
            [isinstance(r, cloud_dns.GCPResourceRecordSet) for r in records])
        assert 6 == len(records)

    assert 1 == len(caplog.records)