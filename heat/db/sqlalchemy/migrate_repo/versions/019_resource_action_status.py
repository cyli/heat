# vim: tabstop=4 shiftwidth=4 softtabstop=4

#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from sqlalchemy import *
from migrate import *


def upgrade(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine

    resource = Table('resource', meta, autoload=True)
    # Align the current state/state_description with the
    # action/status now used in the event table
    Column('action', String(length=255,
                            convert_unicode=False,
                            assert_unicode=None,
                            unicode_error=None,
                            _warn_on_bytestring=False)).create(resource)
    resource.c.state.alter(name='status')
    resource.c.state_description.alter(name='status_reason')


def downgrade(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine

    resource = Table('resource', meta, autoload=True)
    resource.c.status.drop()
    resource.c.status.alter(name='state')
    resource.c.status_reason.alter(name='state_description')