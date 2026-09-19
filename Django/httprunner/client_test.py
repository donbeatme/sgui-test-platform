import json
import unittest
from unittest.mock import patch

from requests import Request, Response
from requests.exceptions import ConnectionError

from httprunner.client import HttpSession, get_req_resp_record
from httprunner.utils import HTTP_BIN_URL


class TestHttpSession(unittest.TestCase):
    def setUp(self):
        self.session = HttpSession()

    def _make_response(self, body, headers=None):
        response = Response()
        response.status_code = 200
        response._content = body
        response.headers.update(headers or {})
        response.request = Request("GET", "http://example.com/export").prepare()
        return response

    def test_request_http(self):
        self.session.request("get", f"{HTTP_BIN_URL}/get")
        address = self.session.data.address
        self.assertGreater(len(address.server_ip), 0)
        self.assertEqual(address.server_port, 80)
        self.assertGreater(len(address.client_ip), 0)
        self.assertGreater(address.client_port, 10000)

    def test_request_https(self):
        self.session.request("get", "https://postman-echo.com/get")
        address = self.session.data.address
        self.assertGreater(len(address.server_ip), 0)
        self.assertEqual(address.server_port, 443)
        self.assertGreater(len(address.client_ip), 0)
        self.assertGreater(address.client_port, 10000)

    def test_request_http_allow_redirects(self):
        self.session.request(
            "get",
            f"{HTTP_BIN_URL}/redirect-to?url=https%3A%2F%2Fgithub.com",
            allow_redirects=True,
        )
        address = self.session.data.address
        self.assertNotEqual(address.server_ip, "N/A")
        self.assertEqual(address.server_port, 443)
        self.assertNotEqual(address.server_ip, "N/A")
        self.assertGreater(address.client_port, 10000)

    def test_request_https_allow_redirects(self):
        self.session.request(
            "get",
            "https://postman-echo.com/redirect-to?url=https%3A%2F%2Fgithub.com",
            allow_redirects=True,
        )
        address = self.session.data.address
        self.assertNotEqual(address.server_ip, "N/A")
        self.assertEqual(address.server_port, 443)
        self.assertNotEqual(address.server_ip, "N/A")
        self.assertGreater(address.client_port, 10000)

    def test_request_http_not_allow_redirects(self):
        self.session.request(
            "get",
            f"{HTTP_BIN_URL}/redirect-to?url=https%3A%2F%2Fgithub.com",
            allow_redirects=False,
        )
        address = self.session.data.address
        self.assertEqual(address.server_ip, "N/A")
        self.assertEqual(address.server_port, 0)
        self.assertEqual(address.client_ip, "N/A")
        self.assertEqual(address.client_port, 0)

    def test_request_https_not_allow_redirects(self):
        self.session.request(
            "get",
            "https://postman-echo.com/redirect-to?url=https%3A%2F%2Fgithub.com",
            allow_redirects=False,
        )
        address = self.session.data.address
        self.assertEqual(address.server_ip, "N/A")
        self.assertEqual(address.server_port, 0)
        self.assertEqual(address.client_ip, "N/A")
        self.assertEqual(address.client_port, 0)

    def test_transport_error_is_recorded_in_response_data(self):
        with patch(
            "requests.Session.request",
            side_effect=ConnectionError("connection refused"),
        ):
            response = self.session.request("get", "http://example.invalid/api")

        self.assertEqual(response.status_code, 0)
        req_resp = self.session.data.req_resps[-1]
        self.assertTrue(req_resp.response.is_transport_error)
        self.assertEqual(req_resp.response.error_type, "ConnectionError")
        self.assertEqual(req_resp.response.error, "connection refused")
        self.assertEqual(
            req_resp.response.body,
            {
                "transport_error": {
                    "type": "ConnectionError",
                    "message": "connection refused",
                }
            },
        )

    def test_binary_response_body_is_omitted_from_record(self):
        xlsx_body = b"PK\x03\x04\x14\x00\x00\x00\x08\x00fake-xlsx-content"
        response = self._make_response(
            xlsx_body,
            {
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                "Content-Disposition": "attachment; filename=report.xlsx",
            },
        )

        req_resp = get_req_resp_record(response)
        body = req_resp.response.body

        self.assertTrue(body["binary"])
        self.assertTrue(body["omitted"])
        self.assertEqual(body["content_length"], len(xlsx_body))
        self.assertEqual(body["filename"], "report.xlsx")
        self.assertNotIn("\x00", json.dumps(req_resp.dict(), ensure_ascii=False))

    def test_json_response_null_character_is_sanitized(self):
        response = self._make_response(
            b'{"value": "a\\u0000b"}',
            {"Content-Type": "application/json"},
        )

        req_resp = get_req_resp_record(response)

        self.assertEqual(req_resp.response.body["value"], "a\\u0000b")
        self.assertNotIn("\x00", json.dumps(req_resp.dict(), ensure_ascii=False))
