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

from octavia_lib.common import constants
from oslo_serialization import jsonutils
from oslo_utils import uuidutils

from ovn_octavia_provider.common import config as ovn_conf
from ovn_octavia_provider.common import constants as ovn_const
from ovn_octavia_provider import helper as ovn_helper
from ovn_octavia_provider.tests.unit import base as ovn_base


class _Row:
    # Hashable by identity, as IDL rows are: the handlers put them in sets.
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _row(**kwargs):
    return _Row(**kwargs)


def _rows(*rows):
    return mock.Mock(rows={index: row for index, row in enumerate(rows)})


class TestUnreachableMembers(ovn_base.TestOvnOctaviaBase):
    """A member the VIP cannot route to is not ONLINE.

    The health check probes from a port on the member's own subnet, so a
    member subnet detached from the VIP's router kept answering it while
    every request to the VIP timed out (measured on the production cluster:
    ONLINE throughout, curl rc=28).
    """

    VIP_PORT = 'vip-port'
    MEMBER = '192.168.242.141'
    POOL = 'pool-1'
    MEMBER_ID = 'member-1'

    def setUp(self):
        super().setUp()
        ovn_conf.register_opts()
        self.helper = ovn_helper.OvnProviderHelper()
        mock.patch.object(self.helper, '_update_status_to_octavia').start()
        self.driver_lib = mock.patch.object(
            self.helper, '_octavia_driver_lib').start()
        self.nb = mock.patch.object(self.helper, 'ovn_nbdb_api').start()
        mock.patch.object(ovn_helper.OvnProviderHelper,
                          'add_request').start()
        self.addCleanup(mock.patch.stopall)
        self.lb = _row(
            uuid=uuidutils.generate_uuid(), name='lb-1',
            external_ids={
                ovn_const.LB_EXT_IDS_VIP_PORT_ID_KEY: self.VIP_PORT,
                ovn_const.LB_EXT_IDS_LS_REFS_KEY: jsonutils.dumps(
                    {'neutron-net-a': 1, 'neutron-net-b': 1}),
                'listener_l-1': '80:pool_%s' % self.POOL,
                'pool_%s' % self.POOL: 'member_%s_%s:80_subnet-b' % (
                    self.MEMBER_ID, self.MEMBER),
                ovn_const.OVN_MEMBER_STATUS_KEY: jsonutils.dumps(
                    {self.MEMBER_ID: constants.ONLINE})})
        self.vip_switch = _row(ports=[
            _row(name=self.VIP_PORT, addresses=['fa:16:3e:00:00:01 '
                                                '192.168.241.10'])])

    def topology(self, router_networks=(), routes=(), on_router=True,
                 switches=None):
        router = _row(
            load_balancer=[self.lb] if on_router else [],
            ports=[_row(networks=list(router_networks))],
            static_routes=[_row(ip_prefix=prefix) for prefix in routes])
        self.nb.tables = {
            'Logical_Router': _rows(router),
            'Logical_Switch': _rows(*(switches if switches is not None
                                      else [self.vip_switch]))}

    def reported(self):
        statuses = jsonutils.loads(
            self.lb.external_ids[ovn_const.OVN_MEMBER_STATUS_KEY])
        return self.helper._withhold_unreachable(self.lb, statuses)[
            self.MEMBER_ID]

    def test_a_member_subnet_on_the_vips_router_stays_online(self):
        self.topology(router_networks=['192.168.241.1/24',
                                       '192.168.242.1/24'])
        self.assertEqual(constants.ONLINE, self.reported())

    def test_a_member_subnet_off_the_vips_router_is_error(self):
        # The production shape: network B detached from the router.
        self.topology(router_networks=['192.168.241.1/24'])
        self.assertEqual(constants.ERROR, self.reported())

    def test_a_static_route_to_the_member_counts(self):
        self.topology(router_networks=['192.168.241.1/24'],
                      routes=['192.168.242.0/24'])
        self.assertEqual(constants.ONLINE, self.reported())

    def test_a_member_on_the_vips_own_switch_needs_no_router(self):
        self.vip_switch.ports.append(
            _row(name='m', addresses=['fa:16:3e:00:00:02 %s' % self.MEMBER]))
        self.topology(on_router=False)
        self.assertEqual(constants.ONLINE, self.reported())

    def test_no_topology_found_changes_nothing(self):
        # Nothing to judge from is not evidence of a fault.
        self.topology(on_router=False, switches=[])
        self.assertEqual(constants.ONLINE, self.reported())

    def test_a_member_that_is_not_online_is_left_alone(self):
        # NO_MONITOR claims nothing, and ERROR is already ERROR.
        self.topology(router_networks=['192.168.241.1/24'])
        statuses = {self.MEMBER_ID: constants.NO_MONITOR}
        self.assertEqual(
            constants.NO_MONITOR,
            self.helper._withhold_unreachable(self.lb, statuses)[
                self.MEMBER_ID])

    def test_the_stored_status_stays_the_probes(self):
        self.topology(router_networks=['192.168.241.1/24'])
        self.reported()
        self.assertEqual(
            {self.MEMBER_ID: constants.ONLINE},
            jsonutils.loads(
                self.lb.external_ids[ovn_const.OVN_MEMBER_STATUS_KEY]))

    def test_ipv6_members_are_judged_too(self):
        member = '2001:db8:2::5'
        self.lb.external_ids['pool_%s' % self.POOL] = (
            'member_%s_%s:80_subnet-b' % (self.MEMBER_ID, member))
        self.topology(router_networks=['2001:db8:1::1/64'])
        self.assertEqual(constants.ERROR, self.reported())
        self.topology(router_networks=['2001:db8:1::1/64',
                                       '2001:db8:2::1/64'])
        self.assertEqual(constants.ONLINE, self.reported())

    def _report(self):
        self.driver_lib.get_loadbalancer.return_value = mock.Mock(
            admin_state_up=True,
            pools=[{'pool_id': self.POOL, 'admin_state_up': True}],
            listeners=[{'listener_id': 'l-1', 'admin_state_up': True}])
        return self.helper._get_current_operating_statuses(self.lb)

    def test_the_report_carries_it_up_to_the_pool(self):
        self.topology(router_networks=['192.168.241.1/24'])
        status = self._report()
        self.assertEqual(
            [constants.ERROR],
            [m[constants.OPERATING_STATUS] for m in status[constants.MEMBERS]])
        self.assertEqual(
            [constants.ERROR],
            [p[constants.OPERATING_STATUS] for p in status[constants.POOLS]])

    def test_a_router_interface_removed_re_reports(self):
        # No member changed, so no health check event would ever say so.
        self.topology(router_networks=['192.168.241.1/24'])
        network = _row(load_balancer=[self.lb], name='neutron-net-b',
                       uuid='ls-b')
        router = _row(load_balancer=[], uuid='lr-1')
        with mock.patch.object(self.helper,
                               '_update_lb_to_lr_association'):
            status = self.helper.lb_delete_lrp_assoc(
                {'network': network, 'router': router})
        self.assertEqual(
            [constants.ERROR],
            [m[constants.OPERATING_STATUS] for m in status[constants.MEMBERS]])

    def test_a_router_interface_added_re_reports(self):
        self.topology(router_networks=['192.168.241.1/24',
                                       '192.168.242.1/24'])
        network = _row(load_balancer=[self.lb], name='neutron-net-b',
                       uuid='ls-b')
        router = _row(load_balancer=[self.lb], uuid='lr-1')
        status = self.helper.lb_create_lrp_assoc(
            {'network': network, 'router': router, 'is_gw_port': False})
        self.assertEqual(
            [constants.ONLINE],
            [m[constants.OPERATING_STATUS] for m in status[constants.MEMBERS]])

    def test_a_load_balancer_without_a_monitor_is_not_re_reported(self):
        self.lb.external_ids[ovn_const.OVN_MEMBER_STATUS_KEY] = '{}'
        self.topology(router_networks=['192.168.241.1/24'])
        network = _row(load_balancer=[self.lb], name='neutron-net-b',
                       uuid='ls-b')
        router = _row(load_balancer=[], uuid='lr-1')
        with mock.patch.object(self.helper,
                               '_update_lb_to_lr_association'):
            self.assertIsNone(self.helper.lb_delete_lrp_assoc(
                {'network': network, 'router': router}))
