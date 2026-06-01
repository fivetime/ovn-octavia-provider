=======================================================
OVN DB Sync Integration with neutron-ovn-db-sync-util
=======================================================

Overview
========

The OVN Octavia provider includes a synchronization plugin that integrates
with Neutron's ``neutron-ovn-db-sync-util`` CLI tool. This allows operators
to synchronize both Neutron and Octavia resources with the OVN Northbound
database in a single operation.

The plugin is automatically discovered when ``ovn-octavia-provider`` is
installed, and is invoked as part of the standard Neutron OVN database
synchronization process.

Plugin Registration
===================

The plugin is registered via setuptools entry point:

.. code-block:: ini

    [entry_points]
    neutron.ovn.db_sync =
        octavia_ovn_sync = ovn_octavia_provider.ovn.db_sync:OctaviaOvnSynchronizer

This allows the Neutron sync utility to automatically discover and load the
Octavia synchronizer when it runs.

Usage
=====

Using with neutron-ovn-db-sync-util
------------------------------------

To synchronize both Neutron and Octavia resources with OVN, run the
``neutron-ovn-db-sync-util`` command:

.. code-block:: bash

    neutron-ovn-db-sync-util \
        --config-file /etc/neutron/neutron.conf \
        --config-file /etc/neutron/plugins/ml2/ml2_conf.ini \
        --ovn-neutron_sync_mode repair

.. note::

    You do NOT need to pass ``/etc/octavia/octavia.conf`` as a config file.
    The Octavia plugin automatically loads it from the standard location
    (``/etc/octavia/octavia.conf``). Passing it would cause conflicts with
    Neutron's database connection configuration.

This will:

1. Synchronize Neutron resources (networks, ports, routers, etc.)
2. Synchronize Octavia load balancers (OVN provider only)
3. Use the same OVN Northbound database connection
4. Apply the same sync mode to all resources

Sync Modes
----------

The Octavia OVN synchronizer supports the following modes:

**repair** (Recommended)
    Synchronizes all Octavia load balancers with the OVN provider with the
    OVN Northbound database. This is the default and recommended mode.

    Example:

    .. code-block:: bash

        neutron-ovn-db-sync-util \
            --config-file /etc/neutron/neutron.conf \
            --config-file /etc/neutron/plugins/ml2/ml2_conf.ini \
            --ovn-neutron_sync_mode repair

**log** (Not Supported)
    The Octavia synchronizer does not support read-only verification mode.
    If you run the sync utility in ``log`` mode, the Octavia plugin will
    skip synchronization and log a warning.

**migrate**
    In migrate mode (used for OVS to OVN migration), the Octavia synchronizer
    behaves the same as in ``repair`` mode, synchronizing all load balancers.

Synchronizing Only Octavia Resources
-------------------------------------

If you only need to synchronize Octavia load balancers without Neutron
resources, you can use either approach:

**Option 1: Using the plugin (Recommended)**

.. code-block:: bash

    neutron-ovn-db-sync-util \
        --config-file /etc/neutron/neutron.conf \
        --config-file /etc/neutron/plugins/ml2/ml2_conf.ini \
        --sync_plugin octavia_ovn_sync \
        --ovn-neutron_sync_mode repair

This will synchronize only Octavia load balancers, skipping Neutron resources.

**Option 2: Using the standalone tool**

.. code-block:: bash

    octavia-ovn-db-sync-util

The standalone tool remains available for compatibility and can be used if you
prefer a dedicated command for Octavia synchronization.

Recommended Workflows
---------------------

**Synchronize everything (Neutron + Octavia):**

.. code-block:: bash

    # Stop services first
    sudo systemctl stop neutron-server octavia-api

    # Run sync
    neutron-ovn-db-sync-util \
        --config-file /etc/neutron/neutron.conf \
        --config-file /etc/neutron/plugins/ml2/ml2_conf.ini \
        --ovn-neutron_sync_mode repair

    # Restart services
    sudo systemctl start neutron-server octavia-api

**Synchronize only Octavia:**

.. code-block:: bash

    # Stop Octavia services first
    sudo systemctl stop octavia-api

    # Run Octavia sync (using plugin)
    neutron-ovn-db-sync-util \
        --config-file /etc/neutron/neutron.conf \
        --config-file /etc/neutron/plugins/ml2/ml2_conf.ini \
        --sync_plugin octavia_ovn_sync \
        --ovn-neutron_sync_mode repair

    # Or use standalone tool
    # octavia-ovn-db-sync-util

    # Restart Octavia
    sudo systemctl start octavia-api

Configuration
=============

Required Configuration Files
-----------------------------

**Configuration passed to neutron-ovn-db-sync-util:**
    - ``/etc/neutron/neutron.conf`` - Core Neutron configuration
    - ``/etc/neutron/plugins/ml2/ml2_conf.ini`` - ML2 plugin configuration
      (includes OVN connection settings)

**Configuration automatically loaded by the plugin:**
    - ``/etc/octavia/octavia.conf`` - Octavia configuration
      (includes Octavia database connection and OVN provider settings)

The Octavia plugin automatically searches for and loads ``octavia.conf`` from
standard locations (``/etc/octavia/octavia.conf``, ``~/octavia.conf``,
``./octavia.conf``). You should **NOT** pass it via ``--config-file`` to
avoid database connection conflicts.

OVN Connection Sharing
----------------------

The Octavia synchronizer reuses the OVN Northbound database connection
established by Neutron. This means:

- Only the OVN connection settings in the Neutron ML2 configuration are used
- No separate OVN connection is created for Octavia synchronization
- Reduced overhead and consistent connection parameters



Limitations
===========

The Octavia OVN synchronizer has the following limitations:

1. **No LOG Mode**: The plugin does not support read-only verification.
   When run in LOG mode, the plugin skips synchronization and logs a warning.

2. **OVN Provider Only**: Only load balancers using the OVN provider are
   synchronized. Load balancers using other providers (e.g., Amphora) are
   not affected.

3. **Octavia Configuration Location**: The plugin expects Octavia configuration
   at ``/etc/octavia/octavia.conf`` or other standard locations. It cannot be
   passed via ``--config-file`` to avoid database connection conflicts.

Best Practices
==============

Pre-Sync Checklist
------------------

Before running the synchronization utility:

1. **Stop Services**: Stop neutron-server and octavia-api to prevent race
   conditions

2. **Backup Databases**: Create backups of both Neutron and Octavia databases

3. **Verify OVN Connection**: Ensure OVN NB database is accessible

Post-Sync Verification
-----------------------

After synchronization completes:

1. **Check Logs**: Review output for errors or warnings
2. **Verify Resources**: Confirm load balancers match between Octavia and OVN
3. **Restart Services**: Bring services back online
