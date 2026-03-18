import unittest

from utils.ssh_manager import SSHConnection


class SSHConnectionPromptFlushTests(unittest.TestCase):
    def setUp(self):
        self.conn = SSHConnection(host="example.com", port=22, username="root")

    def test_interactive_question_flushes_immediately(self):
        text = "[QUESTION] Enter the nameserver subdomain (current: d.mirrorino.ir):"
        self.assertTrue(self.conn._should_flush_immediately(text))

    def test_shell_prompt_flushes_immediately(self):
        self.assertTrue(self.conn._should_flush_immediately("root@host:~# "))

    def test_completed_line_waits_for_normal_flush(self):
        self.assertFalse(self.conn._should_flush_immediately("[INFO] All required tools are available\n"))

    def test_flush_output_splits_large_messages(self):
        parts = []
        self.conn.on_output = lambda text, is_final: parts.append((text, is_final))

        self.conn._flush_output("abcdef", 2)

        self.assertEqual(parts, [("ab", False), ("cd", False), ("ef", False)])


if __name__ == "__main__":
    unittest.main()
