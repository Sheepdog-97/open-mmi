from __future__ import annotations

import contextlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui import config_cli, configuration
from ui.web_dashboard import jellyfin, system_settings


class SystemConfigurationTests(unittest.TestCase):
    def test_environment_file_round_trip_is_private_and_omits_secrets_from_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "dashboard.env"
            values = {
                "OPEN_MMI_JELLYFIN_URL": "https://jellyfin.test:8096",
                "OPEN_MMI_JELLYFIN_USERNAME": "driver",
                "OPEN_MMI_JELLYFIN_PASSWORD": 'secret "value"',
                "OPEN_MMI_JELLYFIN_LIBRARY_ID": "music-1",
            }
            configuration.write_environment_file(values, path)
            self.assertEqual(configuration.read_environment_file(path), values)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            with patch.object(configuration, "dashboard_env_path", return_value=path):
                status = configuration.jellyfin_environment_status(values, {})
            self.assertTrue(status["configured"])
            self.assertTrue(status["password_configured"])
            self.assertNotIn("password", json.dumps(status).lower().replace("password_configured", ""))
            self.assertNotIn("secret", json.dumps(status))

    def test_environment_writer_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real"
            target.write_text("x", encoding="utf-8")
            link = root / "dashboard.env"
            link.symlink_to(target)
            with self.assertRaises(configuration.ConfigurationError):
                configuration.write_environment_file({"OPEN_MMI_JELLYFIN_URL": "https://x.test"}, link)

    def test_payload_preserves_existing_secret_and_switches_auth_modes(self):
        existing = {
            "OPEN_MMI_JELLYFIN_URL": "https://old.test",
            "OPEN_MMI_JELLYFIN_USERNAME": "driver",
            "OPEN_MMI_JELLYFIN_PASSWORD": "saved",
        }
        values = configuration.jellyfin_values_from_payload(
            {
                "url": "https://new.test/",
                "auth_mode": "username",
                "username": "driver",
                "password": "",
                "insecure_tls": False,
                "allow_global": False,
            },
            existing,
        )
        self.assertEqual(values["OPEN_MMI_JELLYFIN_PASSWORD"], "saved")
        self.assertEqual(values["OPEN_MMI_JELLYFIN_URL"], "https://new.test")

        token_values = configuration.jellyfin_values_from_payload(
            {"url": "https://new.test", "auth_mode": "token", "token": "abc", "username": "driver"},
            existing,
        )
        self.assertEqual(token_values["OPEN_MMI_JELLYFIN_TOKEN"], "abc")
        self.assertNotIn("OPEN_MMI_JELLYFIN_PASSWORD", token_values)

    def test_jellyfin_mapping_config_and_connection_test(self):
        config = jellyfin._jellyfin_config_from_mapping(
            {
                "OPEN_MMI_JELLYFIN_URL": "https://jellyfin.test",
                "OPEN_MMI_JELLYFIN_TOKEN": "token",
                "OPEN_MMI_JELLYFIN_USER_ID": "user-1",
            }
        )
        self.assertTrue(config["configured"])
        with patch.object(jellyfin, "_jellyfin_request_json", return_value={"ServerName": "Media", "Version": "10.9"}), patch.object(
            jellyfin, "_jellyfin_user_id", return_value="user-1"
        ):
            result = jellyfin._jellyfin_test_connection(config)
        self.assertEqual(result["server_name"], "Media")
        self.assertEqual(result["user_id"], "user-1")

    def test_system_requests_require_loopback_and_same_origin(self):
        class Handler:
            client_address = ("192.0.2.10", 1234)
            headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}

        self.assertFalse(system_settings._request_allowed(Handler()))
        Handler.client_address = ("127.0.0.1", 1234)
        self.assertTrue(system_settings._request_allowed(Handler()))
        Handler.headers = {"Host": "127.0.0.1:8765", "Origin": "https://evil.test"}
        self.assertFalse(system_settings._request_allowed(Handler()))
        Handler.headers = {"Host": "rebound.evil.test:8765", "Origin": "http://rebound.evil.test:8765"}
        self.assertFalse(system_settings._request_allowed(Handler()))
        Handler.headers = {"Host": "localhost:8765", "Origin": "http://localhost:8765"}
        self.assertTrue(system_settings._request_allowed(Handler()))

    def test_launcher_update_uses_shared_launcher_configuration(self):
        expected = {"ok": True, "launcher": {"default_ui": "tui"}}
        payload = {"default_ui": "tui", "open_at_login": True}
        with patch.object(
            system_settings.owner_config_client, "update_launcher", return_value=expected
        ) as update:
            result = system_settings._update_launcher(payload)
        update.assert_called_once_with(payload)
        self.assertEqual(result, expected)

    def test_launcher_autostart_only_update_does_not_require_json_preferences(self):
        expected = {"ok": True, "launcher": {"open_at_login": False}}
        payload = {"open_at_login": False}
        with patch.object(
            system_settings.owner_config_client, "update_launcher", return_value=expected
        ) as update:
            result = system_settings._update_launcher(payload)
        update.assert_called_once_with(payload)
        self.assertEqual(result, expected)

    def test_vehicle_setup_route_is_local_fixed_and_read_only(self):
        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:8765",
            }

            def __init__(self):
                self.sent = None

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        fixture = {
            "api_version": 1,
            "read_only": True,
            "runtime_mode": "single",
            "catalogue": {"profiles": [], "bindings": []},
            "active": {"state": "ready"},
            "interfaces": [],
        }
        handler = Handler()
        with patch.object(
            system_settings.vehicle_setup,
            "status_payload",
            return_value=fixture,
        ) as status_payload:
            self.assertTrue(
                system_settings._handle_get(handler, "/api/system/vehicle-setup")
            )
        status_payload.assert_called_once_with()
        self.assertEqual(handler.sent, (fixture, 200))

        handler = Handler()
        handler.client_address = ("192.0.2.10", 1234)
        with patch.object(
            system_settings.vehicle_setup,
            "status_payload",
        ) as status_payload:
            self.assertTrue(
                system_settings._handle_get(handler, "/api/system/vehicle-setup")
            )
        status_payload.assert_not_called()
        self.assertEqual(handler.sent[1], 403)

    def test_vehicle_setup_preview_route_is_fixed_local_and_non_mutating(self):
        request = {
            "vehicle": {"source": "maintained", "id": "seat_1p"},
            "bindings": {"source": "maintained", "id": "default"},
            "runtime": {
                "active_bus": "comfort",
                "buses": {"comfort": {"interface": "can0"}},
            },
        }
        body = json.dumps(request).encode("utf-8")

        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:8765",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }

            def __init__(self):
                self.sent = None
                self.rfile = io.BytesIO(body)

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        result = {
            "api_version": 1,
            "read_only": True,
            "apply_available": False,
            "state": "ready",
        }
        handler = Handler()
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_preview",
            return_value=result,
        ) as preview:
            self.assertTrue(
                system_settings._handle_post(
                    handler,
                    "/api/system/vehicle-setup/preview",
                )
            )
        preview.assert_called_once_with(request)
        self.assertEqual(handler.sent, (result, 200))

        handler = Handler()
        handler.client_address = ("192.0.2.10", 1234)
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_preview",
        ) as preview:
            self.assertTrue(
                system_settings._handle_post(
                    handler,
                    "/api/system/vehicle-setup/preview",
                )
            )
        preview.assert_not_called()
        self.assertEqual(handler.sent[1], 403)

    def test_vehicle_setup_apply_route_forwards_only_exact_local_json(self):
        target = {
            "vehicle": {
                "source": "maintained",
                "id": "seat_1p",
                "revision": "sha256:" + "a" * 64,
            },
            "bindings": {
                "source": "maintained",
                "id": "default",
                "revision": "sha256:" + "b" * 64,
            },
            "runtime": {
                "mode": "single",
                "active_bus": "comfort",
                "buses": {"comfort": {"interface": "can0"}},
            },
        }
        request = {
            "target": target,
            "expected_configuration_revision": "sha256:" + "c" * 64,
            "target_configuration_revision": system_settings.vehicle_config_coordinator.vehicle_configuration.selection_revision(
                target
            ),
            "confirm": True,
        }
        body = json.dumps(request).encode("utf-8")

        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:8765",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }

            def __init__(self):
                self.sent = None
                self.rfile = io.BytesIO(body)

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        result = {
            "ok": True,
            "api_version": 1,
            "action": "apply",
            "state": {"state": "complete", "stage": "complete"},
        }
        handler = Handler()
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_apply",
            return_value=result,
        ) as apply:
            self.assertTrue(
                system_settings._handle_post(
                    handler,
                    "/api/system/vehicle-setup/apply",
                )
            )
        apply.assert_called_once_with(request)
        self.assertEqual(handler.sent, (result, 200))

        handler = Handler()
        handler.client_address = ("192.0.2.10", 1234)
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_apply",
        ) as apply:
            self.assertTrue(
                system_settings._handle_post(
                    handler,
                    "/api/system/vehicle-setup/apply",
                )
            )
        apply.assert_not_called()
        self.assertEqual(handler.sent[1], 403)

    def test_vehicle_setup_custom_copy_route_is_local_exact_and_maps_conflicts(self):
        request = {
            "kind": "profile",
            "id": "my-seat",
            "template_source": "maintained",
            "template_id": "seat_1p",
            "template_revision": "sha256:" + "a" * 64,
        }
        body = json.dumps(request).encode("utf-8")

        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:8765",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }

            def __init__(self):
                self.sent = None
                self.rfile = io.BytesIO(body)

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        result = {
            "ok": True,
            "api_version": 1,
            "action": "copy-maintained-template",
            "kind": "profile",
            "custom": {
                "source": "custom",
                "id": "my-seat",
                "revision": "sha256:" + "a" * 64,
            },
        }
        handler = Handler()
        with patch.object(
            system_settings.owner_config_client,
            "create_custom",
            return_value=result,
        ) as copy:
            self.assertTrue(
                system_settings._handle_post(
                    handler,
                    "/api/system/vehicle-custom/create",
                )
            )
        copy.assert_called_once_with(request)
        self.assertEqual(handler.sent, (result, 200))

        handler = Handler()
        handler.client_address = ("192.0.2.10", 1234)
        with patch.object(
            system_settings.owner_config_client,
            "create_custom",
        ) as copy:
            system_settings._handle_post(
                handler,
                "/api/system/vehicle-custom/create",
            )
        copy.assert_not_called()
        self.assertEqual(handler.sent[1], 403)

        handler = Handler()
        with patch.object(
            system_settings.owner_config_client,
            "create_custom",
            side_effect=system_settings.owner_config_client.OwnerConfigConflictError(
                "Maintained template changed",
                "template-stale",
            ),
        ):
            system_settings._handle_post(
                handler,
                "/api/system/vehicle-custom/create",
            )
        self.assertEqual(handler.sent, ({
            "ok": False,
            "code": "template-stale",
            "error": "Maintained template changed",
        }, 409))

    def test_vehicle_custom_load_and_save_routes_are_local_revision_bound(self):
        load_request = {
            "kind": "profile",
            "source": "custom",
            "id": "my-seat",
        }
        save_request = {
            **load_request,
            "expected_revision": "sha256:" + "a" * 64,
            "content": '{"rules":[]}\n',
        }

        class Handler:
            client_address = ("127.0.0.1", 1234)

            def __init__(self, request):
                body = json.dumps(request).encode("utf-8")
                self.headers = {
                    "Host": "127.0.0.1:8765",
                    "Origin": "http://127.0.0.1:8765",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                }
                self.sent = None
                self.rfile = io.BytesIO(body)

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        loaded = {
            "ok": True,
            "api_version": 1,
            "action": "load-custom-item",
            "kind": "profile",
            "custom": {
                "source": "custom",
                "id": "my-seat",
                "revision": "sha256:" + "a" * 64,
            },
            "content": save_request["content"],
            "validation": {"valid": True, "errors": [], "warnings": []},
        }
        handler = Handler(load_request)
        with patch.object(
            system_settings.vehicle_catalogue,
            "load_custom_item",
            return_value=loaded,
        ) as load:
            self.assertTrue(system_settings._handle_post(
                handler, "/api/system/vehicle-custom/load"
            ))
        load.assert_called_once_with(load_request)
        self.assertEqual(handler.sent, (loaded, 200))

        saved = {
            "ok": True,
            "api_version": 1,
            "action": "save-custom-item",
            "kind": "profile",
            "custom": {
                "source": "custom",
                "id": "my-seat",
                "revision": "sha256:" + "b" * 64,
            },
            "validation": {"valid": True, "errors": [], "warnings": []},
            "applied": False,
        }
        handler = Handler(save_request)
        with patch.object(
            system_settings.owner_config_client,
            "save_custom",
            return_value=saved,
        ) as save:
            self.assertTrue(system_settings._handle_post(
                handler, "/api/system/vehicle-custom/save"
            ))
        save.assert_called_once_with(save_request)
        self.assertEqual(handler.sent, (saved, 200))

        handler = Handler(save_request)
        with patch.object(
            system_settings.owner_config_client,
            "save_custom",
            side_effect=system_settings.owner_config_client.OwnerConfigConflictError(
                "Custom catalogue item changed", "custom-stale"
            ),
        ):
            system_settings._handle_post(
                handler, "/api/system/vehicle-custom/save"
            )
        self.assertEqual(handler.sent[1], 409)
        self.assertEqual(handler.sent[0]["code"], "custom-stale")

        handler = Handler(load_request)
        handler.client_address = ("192.0.2.10", 1234)
        with patch.object(
            system_settings.vehicle_catalogue, "load_custom_item"
        ) as load:
            system_settings._handle_post(
                handler, "/api/system/vehicle-custom/load"
            )
        load.assert_not_called()
        self.assertEqual(handler.sent[1], 403)

    def test_vehicle_custom_import_route_is_local_bounded_and_creation_only(self):
        request = {
            "kind": "profile",
            "id": "imported-seat",
            "content": '{"rules":[],"presence":[],"status":[]}\n',
        }

        class Handler:
            client_address = ("127.0.0.1", 1234)

            def __init__(self):
                body = json.dumps(request).encode("utf-8")
                self.headers = {
                    "Host": "127.0.0.1:8765",
                    "Origin": "http://127.0.0.1:8765",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                }
                self.sent = None
                self.rfile = io.BytesIO(body)

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        result = {
            "ok": True,
            "api_version": 1,
            "action": "import-custom-item",
            "kind": "profile",
            "custom": {
                "source": "custom",
                "id": "imported-seat",
                "revision": "sha256:" + "a" * 64,
            },
            "validation": {"valid": True, "errors": [], "warnings": []},
            "applied": False,
        }
        handler = Handler()
        with patch.object(
            system_settings.owner_config_client,
            "import_custom",
            return_value=result,
        ) as import_item:
            self.assertTrue(system_settings._handle_post(
                handler, "/api/system/vehicle-custom/import"
            ))
        import_item.assert_called_once_with(request)
        self.assertEqual(handler.sent, (result, 200))

        handler = Handler()
        with patch.object(
            system_settings.owner_config_client,
            "import_custom",
            side_effect=system_settings.owner_config_client.OwnerConfigConflictError(
                "A custom catalogue item with that id already exists",
                "custom-exists",
            ),
        ):
            system_settings._handle_post(
                handler, "/api/system/vehicle-custom/import"
            )
        self.assertEqual(handler.sent[1], 409)
        self.assertEqual(handler.sent[0]["code"], "custom-exists")

        handler = Handler()
        handler.client_address = ("192.0.2.10", 1234)
        with patch.object(
            system_settings.owner_config_client, "import_custom"
        ) as import_item:
            system_settings._handle_post(
                handler, "/api/system/vehicle-custom/import"
            )
        import_item.assert_not_called()
        self.assertEqual(handler.sent[1], 403)

    def test_vehicle_custom_manage_route_is_local_and_revision_bound(self):
        request = {
            "action": "rename",
            "kind": "profile",
            "source": "custom",
            "id": "my-seat",
            "expected_revision": "sha256:" + "a" * 64,
            "new_id": "driver-seat",
        }
        body = json.dumps(request).encode("utf-8")

        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:8765",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }

            def __init__(self):
                self.sent = None
                self.rfile = io.BytesIO(body)

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        result = {
            "ok": True,
            "action": "manage-custom-item",
            "operation": "rename",
            "custom": {
                "source": "custom",
                "id": "driver-seat",
                "revision": "sha256:" + "a" * 64,
            },
            "applied": False,
        }
        handler = Handler()
        with patch.object(
            system_settings.owner_config_client,
            "manage_custom",
            return_value=result,
        ) as manage:
            self.assertTrue(system_settings._handle_post(
                handler, "/api/system/vehicle-custom/manage"
            ))
        manage.assert_called_once_with(request)
        self.assertEqual(handler.sent, (result, 200))

        handler = Handler()
        with patch.object(
            system_settings.owner_config_client,
            "manage_custom",
            side_effect=system_settings.owner_config_client.OwnerConfigConflictError(
                "Active custom catalogue items cannot be renamed or deleted",
                "custom-active",
            ),
        ):
            system_settings._handle_post(
                handler, "/api/system/vehicle-custom/manage"
            )
        self.assertEqual(handler.sent[1], 409)
        self.assertEqual(handler.sent[0]["code"], "custom-active")

        handler = Handler()
        handler.client_address = ("192.0.2.10", 1234)
        with patch.object(
            system_settings.owner_config_client, "manage_custom"
        ) as manage:
            system_settings._handle_post(
                handler, "/api/system/vehicle-custom/manage"
            )
        manage.assert_not_called()
        self.assertEqual(handler.sent[1], 403)

    def test_vehicle_custom_save_has_a_bounded_larger_body_limit(self):
        content = " " * (system_settings.SYSTEM_MAX_BODY_BYTES + 1)
        request = {
            "kind": "profile",
            "source": "custom",
            "id": "my-seat",
            "expected_revision": "sha256:" + "a" * 64,
            "content": content,
        }
        body = json.dumps(request).encode("utf-8")

        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:8765",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }

            def __init__(self):
                self.sent = None
                self.rfile = io.BytesIO(body)

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        handler = Handler()
        with patch.object(
            system_settings.owner_config_client,
            "save_custom",
            return_value={"ok": True},
        ) as save:
            system_settings._handle_post(
                handler, "/api/system/vehicle-custom/save"
            )
        save.assert_called_once_with(request)
        self.assertEqual(handler.sent, ({"ok": True}, 200))

    def test_vehicle_setup_apply_route_maps_conflict_failure_and_unavailable(self):
        body = b'{"confirm":true}'

        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:8765",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }

            def __init__(self):
                self.sent = None
                self.rfile = io.BytesIO(body)

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        handler = Handler()
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_apply",
            side_effect=system_settings.vehicle_config_coordinator.CoordinatorConflictError(
                "Vehicle configuration preview is stale",
                "stale-preview",
            ),
        ):
            system_settings._handle_post(
                handler,
                "/api/system/vehicle-setup/apply",
            )
        self.assertEqual(handler.sent[1], 409)
        self.assertEqual(handler.sent[0]["code"], "stale-preview")

        handler = Handler()
        state = {"state": "failed", "stage": "restored"}
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_apply",
            side_effect=system_settings.vehicle_config_coordinator.CoordinatorApplyError(
                "Vehicle configuration operation failed during installing",
                "apply-failed-restored",
                state,
            ),
        ):
            system_settings._handle_post(
                handler,
                "/api/system/vehicle-setup/apply",
            )
        self.assertEqual(handler.sent[1], 500)
        self.assertEqual(handler.sent[0]["state"], state)

        handler = Handler()
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_apply",
            side_effect=system_settings.vehicle_config_coordinator.CoordinatorUnavailableError(
                "Vehicle configuration coordinator is unavailable"
            ),
        ):
            system_settings._handle_post(
                handler,
                "/api/system/vehicle-setup/apply",
            )
        self.assertEqual(handler.sent[1], 502)

    def test_system_json_rejects_duplicate_fields_before_apply(self):
        body = b'{"confirm":true,"confirm":false}'

        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {
                "Host": "127.0.0.1:8765",
                "Origin": "http://127.0.0.1:8765",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }

            def __init__(self):
                self.sent = None
                self.rfile = io.BytesIO(body)

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        handler = Handler()
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_apply",
        ) as apply:
            self.assertTrue(
                system_settings._handle_post(
                    handler,
                    "/api/system/vehicle-setup/apply",
                )
            )
        apply.assert_not_called()
        self.assertEqual(handler.sent[1], 400)
        self.assertEqual(handler.sent[0]["error"], "Invalid JSON request")


    def test_vehicle_configuration_coordinator_status_route_uses_fixed_socket_client(self):
        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
            sent = None

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        handler = Handler()
        fixture = {
            "ok": True,
            "read_only": True,
            "apply_enabled": False,
            "state": {"state": "idle"},
        }
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_status",
            return_value=fixture,
        ) as status:
            self.assertTrue(
                system_settings._handle_get(
                    handler,
                    "/api/system/vehicle-setup/coordinator",
                )
            )
        status.assert_called_once_with()
        self.assertEqual(handler.sent, (fixture, 200))

        handler = Handler()
        handler.client_address = ("192.0.2.10", 1234)
        with patch.object(
            system_settings.vehicle_config_coordinator,
            "client_status",
        ) as status:
            self.assertTrue(
                system_settings._handle_get(
                    handler,
                    "/api/system/vehicle-setup/coordinator",
                )
            )
        status.assert_not_called()
        self.assertEqual(handler.sent[1], 403)

    def test_update_status_routes_are_local_fixed_and_read_only(self):
        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}

            def __init__(self):
                self.sent = None
                self.rfile = io.BytesIO()

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        fixture = {"api_version": 1, "read_only": True, "update": {"state": "not-checked"}}
        handler = Handler()
        with patch.object(system_settings.update_status, "status_payload", return_value=fixture) as status_payload:
            self.assertTrue(system_settings._handle_get(handler, "/api/system/update-status"))
        status_payload.assert_called_once_with()
        self.assertEqual(handler.sent, (fixture, 200))

        body = b'{"confirm": true}'
        handler = Handler()
        handler.headers = {
            "Host": "127.0.0.1:8765",
            "Origin": "http://127.0.0.1:8765",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        handler.rfile = io.BytesIO(body)
        checked = {"api_version": 1, "read_only": True, "update": {"state": "up-to-date"}}
        with patch.object(system_settings.update_coordinator, "client_check", return_value=checked) as check:
            self.assertTrue(system_settings._handle_post(handler, "/api/system/update-check"))
        check.assert_called_once_with()
        self.assertEqual(handler.sent, (checked, 200))

    def test_update_readiness_route_is_local_and_has_no_caller_parameters(self):
        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
            sent = None
            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        handler = Handler()
        status = {"readiness": {"state": "ready", "blockers": []}}
        readiness = {"api_version": 1, "read_only": True, "state": "blocked", "install_allowed": False}
        with patch.object(system_settings.update_status, "status_payload", return_value=status), patch.object(
            system_settings.update_readiness, "readiness_payload", return_value=readiness
        ) as inspect:
            self.assertTrue(system_settings._handle_get(handler, "/api/system/update-readiness"))
        inspect.assert_called_once_with(status)
        self.assertEqual(handler.sent, (readiness, 200))

    def test_update_coordinator_status_route_uses_the_fixed_local_socket_client(self):
        class Handler:
            client_address = ("127.0.0.1", 1234)
            headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"}
            sent = None

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        handler = Handler()
        coordinator = {"ok": True, "installation_enabled": True, "state": {"state": "idle"}}
        with patch.object(system_settings.update_coordinator, "client_status", return_value=coordinator) as status:
            self.assertTrue(system_settings._handle_get(handler, "/api/system/update-coordinator"))
        status.assert_called_once_with()
        self.assertEqual(handler.sent, (coordinator, 200))

    def test_update_prepare_accepts_only_fixed_confirmation(self):
        class Handler:
            client_address = ("127.0.0.1", 1234)
            def __init__(self, body):
                self.headers = {
                    "Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765",
                    "Content-Type": "application/json", "Content-Length": str(len(body)),
                }
                self.rfile = io.BytesIO(body)
                self.sent = None
            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        prepared = {"ok": True, "execution_enabled": False, "state": {"state": "prepared"}}
        handler = Handler(b'{"confirm": true}')
        with patch.object(system_settings.update_coordinator, "client_prepare", return_value=prepared) as prepare:
            self.assertTrue(system_settings._handle_post(handler, "/api/system/update-prepare"))
        prepare.assert_called_once_with()
        self.assertEqual(handler.sent, (prepared, 200))

        handler = Handler(b'{"confirm": true, "ref": "main"}')
        with patch.object(system_settings.update_coordinator, "client_prepare") as prepare:
            self.assertTrue(system_settings._handle_post(handler, "/api/system/update-prepare"))
        prepare.assert_not_called()
        self.assertEqual(handler.sent[1], 400)

    def test_update_install_accepts_only_fixed_confirmation(self):
        class Handler:
            client_address = ("127.0.0.1", 1234)

            def __init__(self, body):
                self.headers = {
                    "Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765",
                    "Content-Type": "application/json", "Content-Length": str(len(body)),
                }
                self.rfile = io.BytesIO(body)
                self.sent = None

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        complete = {"ok": True, "installation_enabled": True, "state": {"state": "complete"}}
        handler = Handler(b'{"confirm": true}')
        with patch.object(system_settings.update_coordinator, "client_install", return_value=complete) as install:
            self.assertTrue(system_settings._handle_post(handler, "/api/system/update-install"))
        install.assert_called_once_with()
        self.assertEqual(handler.sent, (complete, 200))

        handler = Handler(b'{"confirm": true, "command": "sh"}')
        with patch.object(system_settings.update_coordinator, "client_install") as install:
            self.assertTrue(system_settings._handle_post(handler, "/api/system/update-install"))
        install.assert_not_called()
        self.assertEqual(handler.sent[1], 400)

    def test_update_check_rejects_caller_supplied_source_fields(self):
        class Handler:
            client_address = ("127.0.0.1", 1234)

            def __init__(self, body: bytes):
                self.headers = {
                    "Host": "127.0.0.1:8765",
                    "Origin": "http://127.0.0.1:8765",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                }
                self.rfile = io.BytesIO(body)
                self.sent = None

            def _send_json(self, payload, status=200):
                self.sent = (payload, status)

        handler = Handler(b'{"repository": "https://evil.test/repo", "branch": "main"}')
        with patch.object(system_settings.update_status, "check_for_updates") as check:
            self.assertTrue(system_settings._handle_post(handler, "/api/system/update-check"))
        check.assert_not_called()
        self.assertEqual(handler.sent[1], 400)
        self.assertIn("Invalid update check request", handler.sent[0]["error"])

    def test_cli_setup_writes_credentials_without_printing_secrets(self):
        values = {
            "OPEN_MMI_JELLYFIN_URL": "https://jellyfin.test",
            "OPEN_MMI_JELLYFIN_USERNAME": "driver",
            "OPEN_MMI_JELLYFIN_PASSWORD": "never-print-this",
        }
        output = io.StringIO()
        with patch.object(config_cli, "_require_root_media_authority"), patch.object(
            config_cli, "_setup_jellyfin", return_value=values
        ), patch.object(
            config_cli, "_jellyfin_test_candidate", return_value={"connected": True}
        ), patch.object(
            config_cli.media_egress_config, "write_jellyfin", return_value={}
        ) as write, patch.object(
            config_cli.media_egress_config, "jellyfin_status", return_value={
                "configured": True,
                "password_configured": True,
                "token_configured": False,
            }
        ), patch.object(config_cli, "_restart_media_egress"), contextlib.redirect_stdout(output):
            result = config_cli.main(["jellyfin", "setup"])
        self.assertEqual(result, 0)
        write.assert_called_once_with(values)
        rendered = output.getvalue()
        self.assertIn('"configured": true', rendered.lower())
        self.assertNotIn("never-print-this", rendered)

    def test_cli_launcher_autostart_uses_shared_launcher_helpers(self):
        output = io.StringIO()
        with patch.object(config_cli.launcher, "configure_open_at_login") as configure, patch.object(
            config_cli.launcher, "open_at_login_enabled", return_value=False
        ), patch.object(
            config_cli.launcher, "default_autostart_path", return_value=Path("/tmp/open-mmi.desktop")
        ), contextlib.redirect_stdout(output):
            result = config_cli.main(["launcher", "autostart", "disable"])
        self.assertEqual(result, 0)
        configure.assert_called_once_with(False)
        self.assertIn('"open_at_login": false', output.getvalue().lower())

    def test_cli_updates_status_and_check_use_fixed_backend_actions(self):
        output = io.StringIO()
        fixture = {"channel": "nightly", "update": {"state": "not-checked"}}
        with patch.object(config_cli.update_status, "status_payload", return_value=fixture) as status, contextlib.redirect_stdout(output):
            result = config_cli.main(["updates", "status"])
        self.assertEqual(result, 0)
        status.assert_called_once_with()
        self.assertIn('"channel": "nightly"', output.getvalue())

        output = io.StringIO()
        checked = {"channel": "nightly", "update": {"state": "up-to-date"}}
        with patch.object(config_cli.update_coordinator, "client_check", return_value=checked) as check, contextlib.redirect_stdout(output):
            result = config_cli.main(["updates", "check"])
        self.assertEqual(result, 0)
        check.assert_called_once_with()
        self.assertIn('"state": "up-to-date"', output.getvalue())

    def test_cli_channel_selection_accepts_only_named_policy(self):
        output = io.StringIO()
        fixture = {"channel": "beta", "update": {"state": "not-checked"}}
        with patch.object(config_cli.update_status, "configure_channel", return_value=fixture) as configure, contextlib.redirect_stdout(output):
            result = config_cli.main(["updates", "channel", "beta"])
        self.assertEqual(result, 0)
        configure.assert_called_once_with("beta")
        self.assertIn('"channel": "beta"', output.getvalue())
        with self.assertRaises(SystemExit):
            config_cli.build_parser().parse_args(["updates", "channel", "development"])

    def test_cli_install_sends_only_the_fixed_coordinator_action(self):
        output = io.StringIO()
        fixture = {"ok": True, "state": {"state": "complete"}}
        with patch.object(config_cli.update_coordinator, "client_install", return_value=fixture) as install, contextlib.redirect_stdout(output):
            result = config_cli.main(["updates", "install"])
        self.assertEqual(result, 0)
        install.assert_called_once_with()
        self.assertIn('"state": "complete"', output.getvalue())

    def test_cli_vehicle_configuration_coordinator_uses_fixed_status_action(self):
        output = io.StringIO()
        fixture = {
            "ok": True,
            "read_only": True,
            "apply_enabled": False,
            "state": {"state": "idle"},
        }
        with patch.object(
            config_cli.vehicle_config_coordinator,
            "client_status",
            return_value=fixture,
        ) as status, contextlib.redirect_stdout(output):
            result = config_cli.main(["vehicle-setup", "coordinator"])
        self.assertEqual(result, 0)
        status.assert_called_once_with()
        self.assertIn('"apply_enabled": false', output.getvalue().lower())

    def test_cli_dashboard_enable_remains_advanced_service_control(self):
        output = io.StringIO()
        with patch.object(config_cli.launcher, "configure_dashboard_service") as configure, contextlib.redirect_stdout(output):
            result = config_cli.main(["dashboard", "enable"])
        self.assertEqual(result, 0)
        configure.assert_called_once_with("enable")
        self.assertIn('"action": "enable"', output.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
