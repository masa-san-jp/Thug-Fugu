import unittest
from unittest import mock

from fugu_local.cli import main


class CliTests(unittest.TestCase):
    def test_unsafe_bind_exits_before_serving_without_opt_in(self):
        with mock.patch("fugu_local.cli.serve") as serve_mock:
            code = main(
                [
                    "serve",
                    "--config",
                    "examples/fugu-local.echo.json",
                    "--host",
                    "0.0.0.0",
                ]
            )

        self.assertEqual(code, 2)
        serve_mock.assert_not_called()

    def test_unsafe_bind_opt_in_reaches_serve(self):
        with mock.patch("fugu_local.cli.serve", side_effect=KeyboardInterrupt) as serve_mock:
            with self.assertRaises(KeyboardInterrupt):
                main(
                    [
                        "serve",
                        "--config",
                        "examples/fugu-local.echo.json",
                        "--host",
                        "0.0.0.0",
                        "--allow-unsafe-bind",
                    ]
                )

        self.assertTrue(serve_mock.call_args.kwargs["allow_unsafe_bind"])


if __name__ == "__main__":
    unittest.main()
