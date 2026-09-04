from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse


class HealthCheckTests(TestCase):
    def test_liveness_es_pequeno_y_no_consulta_base(self):
        with self.assertNumQueries(0):
            response = self.client.get(reverse("health-live"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertNotIn("sessionid", response.cookies)

    def test_readiness_confirma_postgresql_con_una_consulta(self):
        with self.assertNumQueries(1):
            response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertNotIn("sessionid", response.cookies)

    def test_readiness_falla_sin_exponer_detalles(self):
        detalle_sensible = "password=secreto host=interno"
        with patch(
            "config.views.connection.cursor",
            side_effect=DatabaseError(detalle_sensible),
        ):
            response = self.client.get(reverse("health-ready"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable")
        self.assertNotContains(response, detalle_sensible, status_code=503)

    def test_health_checks_solo_aceptan_get_y_head(self):
        for route_name in ("health-live", "health-ready"):
            with self.subTest(route_name=route_name):
                self.assertEqual(
                    self.client.head(reverse(route_name)).status_code,
                    200,
                )
                self.assertEqual(
                    self.client.post(reverse(route_name)).status_code,
                    405,
                )

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_health_checks_no_redirigen_el_sondeo_http_interno(self):
        self.assertEqual(self.client.get(reverse("health-live")).status_code, 200)
        self.assertEqual(self.client.get(reverse("health-ready")).status_code, 200)
