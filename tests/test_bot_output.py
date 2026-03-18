import os
import unittest

from bot.main import format_terminal_output


class FormatTerminalOutputTests(unittest.TestCase):
    def test_escapes_html_sensitive_terminal_text(self):
        rendered = format_terminal_output('bash <(curl -Ls https://example.com?a=1&b=2)')
        self.assertEqual(
            rendered,
            ['<pre>bash &lt;(curl -Ls https://example.com?a=1&amp;b=2)</pre>'],
        )

    def test_splits_output_by_max_length(self):
        previous = os.environ.get('MAX_OUTPUT_LENGTH')
        os.environ['MAX_OUTPUT_LENGTH'] = '4'
        try:
            rendered = format_terminal_output('abcdefgh')
        finally:
            if previous is None:
                os.environ.pop('MAX_OUTPUT_LENGTH', None)
            else:
                os.environ['MAX_OUTPUT_LENGTH'] = previous

        self.assertEqual(rendered, ['<pre>abcd</pre>', '<pre>efgh</pre>'])


if __name__ == '__main__':
    unittest.main()
