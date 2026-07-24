# Automatic suspend from CAN silence

Open MMI can optionally suspend the host after the active physical SocketCAN bus
has remained completely silent for a configured interval. This is implemented by
the standalone `open-mmi-powerd` system service. It is not a canonical vehicle
action and it does not run through the event-to-action dispatcher.

The policy is vehicle-independent: it follows the interface selected by Vehicle
Setup and contains no CAN IDs, manufacturer names, or payload matching. Each
vehicle integration still needs to qualify that its selected bus becomes quiet
when the vehicle sleeps and becomes active when the vehicle wakes.

The feature is disabled by default. Enable the tested 60-second policy with:

```bash
sudo ./scripts/manage.sh power enable 60
```

Inspect or disable it with:

```bash
sudo ./scripts/manage.sh power status
sudo ./scripts/manage.sh power disable
```

## Suspend decision

Before requesting suspend, `open-mmi-powerd` requires all of the following:

- the loaded Open MMI runtime reports a ready physical `canN` interface;
- at least one CAN frame has been observed since service start or the previous
  resume;
- that interface still exists, is administratively up, and is not BUS-OFF,
  stopped, disconnected, or unknown;
- the USB adapter and its host PCI wake ancestry expose enabled wake controls;
- no Open MMI update, vehicle-configuration, or lifecycle transaction holds its
  lock;
- the configured CAN-silence and post-resume guard intervals have elapsed.

A missing adapter, failed interface, BUS-OFF controller, invalid runtime status,
untrusted transaction lock, or unverified wake path fails closed and is not
interpreted as vehicle sleep.

The installer also deploys `open-mmi.conf` under
`/usr/lib/tmpfiles.d`. systemd-tmpfiles recreates `/run/open-mmi` and the
lifecycle, update, and vehicle-configuration lock files during every boot.
The installer still validates their ownership, type, link count, and
permissions before using them. Missing or untrusted lock files fail closed and
inhibit automatic suspend.

The installer also deploys `90-open-mmi-can-wake.rules`. When a physical
`canN` interface is added or changed, udev runs the packaged
`open-mmi-powerd wake-enable` helper. The helper validates that the topology
contains both a direct USB device and a PCI host controller before enabling
every exposed `power/wakeup` control in that ancestry. Unsupported adapters
remain fail-closed, and no partial topology is modified. The rule is reloaded
and retriggered during installation and updates, so an already-connected
adapter is handled without requiring a replug.

`systemctl suspend` returns after resume. The daemon then closes and reopens its
CAN observation socket and requires fresh CAN traffic before another suspend can
occur. This prevents immediate suspend loops after resume or after a failed
suspend request.

## Vehicle qualification

A vehicle profile does not contain a special wake frame. Qualification confirms
that its selected physical bus:

1. becomes fully quiet when the vehicle has shut down;
2. does not remain silent for the configured interval during normal active use;
3. reliably produces valid traffic when the vehicle wakes; and
4. uses a CAN adapter and host USB path capable of remote wake.

Vehicles whose selected bus continues periodic background traffic will simply
remain awake. Vehicles whose active bus can legitimately remain silent for long
periods should leave this policy disabled or use a longer qualified interval.

## Policy file

The root-owned policy is stored at `/etc/open-mmi/power-policy.json`:

```json
{
  "schema_version": 1,
  "enabled": true,
  "trigger": "can_bus_silence",
  "silence_seconds": 60,
  "require_remote_wake": true,
  "resume_guard_seconds": 30
}
```

The policy is installation configuration, not vehicle-specific CAN knowledge.
Future trigger types can be added without changing the meaning of
`can_bus_silence`.

## Wake-path diagnostics

Check the currently selected interface after the adapter has been attached:

```bash
sudo /opt/open-mmi/venv/bin/python - <<'PY'
from powerd.wake import remote_wake_ready
print(remote_wake_ready("can0"))
PY
```

The result must be `True` when `require_remote_wake` is enabled. To re-run the
managed helper manually while diagnosing a device, use:

```bash
sudo /opt/open-mmi/venv/bin/open-mmi-powerd wake-enable --interface can0
```

A non-zero exit means the direct USB device or PCI host controller did not expose
a complete writable wake path.

### CAN interface availability during boot

The runtime status may identify a CAN interface before the kernel device exists
or before vehicle configuration has brought it up. `open-mmi-powerd` treats that
as an unavailable observation source: it waits with a bounded retry interval and
does not open SocketCAN until the interface is present, administratively up and
in a healthy controller state. If an active observation socket later reports a
disconnect or `ENETDOWN`, the daemon closes it, requires fresh CAN traffic and
reopens it only after the interface becomes healthy again.
