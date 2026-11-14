"""Ожидание WSL/Docker и диагностика завершения локального запуска."""

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dashboard.deploy import local


class LocalLauncherTests(unittest.TestCase):
    @patch.object(local.time, "sleep")
    @patch.object(local.subprocess, "run")
    def test_waits_until_docker_is_available_inside_ubuntu(self, run, sleep):
        run.side_effect = [subprocess.TimeoutExpired("wsl.exe", 10),
                           SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)]
        local.wait_for_docker()
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(run.call_args.args[0][:6],
                         ["wsl.exe", "-d", "Ubuntu", "--exec", "docker", "info"])

    @patch.object(local.time, "monotonic", side_effect=[0, 0, 2])
    @patch.object(local.time, "sleep")
    @patch.object(local.subprocess, "run", return_value=SimpleNamespace(returncode=1))
    def test_docker_timeout_explains_wsl_integration(self, run, sleep, clock):
        with self.assertRaisesRegex(SystemExit, "WSL integration"):
            local.wait_for_docker(timeout=1)

    @patch.object(local.time, "monotonic", side_effect=[0, 0, 31, 91])
    @patch.object(local.time, "sleep")
    @patch.object(local, "healthy", side_effect=[False, False, True])
    def test_dashboard_waits_beyond_one_minute_while_process_is_alive(self, health, sleep, clock):
        process = Mock()
        process.poll.return_value = None
        local.wait_for_dashboard(process, "application.log")
        self.assertEqual(process.poll.call_count, 2)
        self.assertEqual(sleep.call_count, 2)

    @patch.object(local, "healthy", return_value=False)
    def test_failed_process_reports_exit_and_log_instead_of_waiting(self, health):
        process = Mock()
        process.poll.return_value = 1
        with self.assertRaisesRegex(SystemExit, r"exited \(1\).*application.log"):
            local.wait_for_dashboard(process, "application.log")


if __name__ == "__main__":
    unittest.main()
