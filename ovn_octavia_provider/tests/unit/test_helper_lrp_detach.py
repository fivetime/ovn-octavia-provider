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

from unittest import mock

from oslo_serialization import jsonutils
from oslo_utils import uuidutils
from ovsdbapp.backend.ovs_idl import idlutils

from ovn_octavia_provider.common import config as ovn_conf
from ovn_octavia_provider.common import constants as ovn_const
from ovn_octavia_provider import helper as ovn_helper
from ovn_octavia_provider.tests.unit import base as ovn_base


class _Row:
    # Hashable by identity, as IDL rows are: the handler puts them in sets.
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestDetachingAMemberNetwork(ovn_base.TestOvnOctaviaBase):
    """Detaching a member's network leaves the load balancer on its VIP's.

    A member on network B gives its load balancer a reference on B. Taking
    B off the router used to take the load balancer off the router, and so
    off every switch of the router -- its own VIP switch A included: the
    whole load balancer stopped answering (testbed: 0 of 10 requests, every
    member reported ONLINE).
    """

    VIP_PORT = 'vip-port'

    def setUp(self):
        super().setUp()
        ovn_conf.register_opts()
        self.helper = ovn_helper.OvnProviderHelper()
        self.nb = mock.patch.object(self.helper, 'ovn_nbdb_api').start()
        self.lr_assoc = mock.patch.object(
            self.helper, '_update_lb_to_lr_association').start()
        self.ls_assoc = mock.patch.object(
            self.helper, '_update_lb_to_ls_association').start()
        mock.patch.object(self.helper, '_statuses_after_topology_change',
                          return_value={}).start()
        self.addCleanup(mock.patch.stopall)
        self.lb = self.load_balancer({ovn_const.LB_EXT_IDS_VIP_PORT_ID_KEY:
                                      self.VIP_PORT})
        self.vip_switch = _Row(name='neutron-net-a',
                               ports=[_Row(name=self.VIP_PORT)])
        self.member_switch = _Row(name='neutron-net-b',
                                  load_balancer=[self.lb], ports=[])
        self.switches = {'neutron-net-a': self.vip_switch}
        self.nb.lookup.side_effect = self.lookup
        self.router = _Row(name='neutron-r', uuid=uuidutils.generate_uuid(),
                           load_balancer=[self.lb],
                           ports=[self.router_port('net-a', '10.0.0.1/24')])

    def load_balancer(self, extra):
        external_ids = {ovn_const.LB_EXT_IDS_LS_REFS_KEY: jsonutils.dumps(
            {'neutron-net-a': 1, 'neutron-net-b': 1})}
        external_ids.update(extra)
        return _Row(uuid=uuidutils.generate_uuid(), name='lb-1',
                    external_ids=external_ids)

    @staticmethod
    def router_port(network, cidr):
        return _Row(gateway_chassis=[], ha_chassis_group=[], networks=[cidr],
                    external_ids={
                        ovn_const.OVN_NETWORK_NAME_EXT_ID_KEY: network})

    def lookup(self, table, name):
        if name not in self.switches:
            raise idlutils.RowNotFound(table=table, col='name', match=name)
        return self.switches[name]

    def detach(self, network):
        self.helper.lb_delete_lrp_assoc(
            {'network': network, 'router': self.router})

    def test_the_load_balancer_stays_on_the_router_and_its_vip_switch(self):
        self.detach(self.member_switch)
        self.lr_assoc.assert_not_called()
        self.ls_assoc.assert_not_called()

    def test_detaching_the_vip_network_itself_still_removes_it(self):
        vip_network = _Row(name='neutron-net-a', load_balancer=[self.lb],
                           ports=[_Row(name=self.VIP_PORT)])
        self.router.ports = [self.router_port('net-b', '10.0.1.1/24')]
        self.switches = {'neutron-net-b': _Row(name='neutron-net-b',
                                               ports=[])}
        self.detach(vip_network)
        self.lr_assoc.assert_called_once_with(self.lb, self.router,
                                              delete=True)

    def test_an_additional_vip_on_the_router_keeps_it_too(self):
        self.lb = self.load_balancer({
            ovn_const.LB_EXT_IDS_VIP_PORT_ID_KEY: 'elsewhere',
            ovn_const.LB_EXT_IDS_ADDIT_VIP_PORT_ID_KEY: 'v6-port,' +
            self.VIP_PORT})
        self.member_switch.load_balancer = [self.lb]
        self.router.load_balancer = [self.lb]
        self.detach(self.member_switch)
        self.lr_assoc.assert_not_called()

    def test_unreadable_topology_leaves_the_removal_as_it_was(self):
        self.nb.lookup.side_effect = RuntimeError('idl gone')
        self.detach(self.member_switch)
        self.lr_assoc.assert_called_once_with(self.lb, self.router,
                                              delete=True)
