from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


MIGRACION_FINAL = [("inventory", "0003_pedidos_y_cantidades_bigint")]


class PruebaMigracionRestaurable(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.ids_incompatibles = []
        self.addCleanup(self._comprobar_esquema_final)
        self.addCleanup(self._restaurar_esquema_final)

    def _restaurar_esquema_final(self):
        executor = MigrationExecutor(connection)
        if (
            "inventory",
            "0003_pedidos_y_cantidades_bigint",
        ) not in executor.loader.applied_migrations:
            estado = executor.loader.project_state(
                [("inventory", "0002_movimientoinventario")]
            )
            Movimiento = estado.apps.get_model(
                "inventory", "MovimientoInventario"
            )
            if self.ids_incompatibles:
                Movimiento.objects.filter(pk__in=self.ids_incompatibles).delete()
        MigrationExecutor(connection).migrate(MIGRACION_FINAL)

    def _comprobar_esquema_final(self):
        executor = MigrationExecutor(connection)
        self.assertIn(
            ("inventory", "0003_pedidos_y_cantidades_bigint"),
            executor.loader.applied_migrations,
        )


class MigracionTokenCheckoutTests(PruebaMigracionRestaurable):
    def test_tokens_historicos_default_y_unicidad(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("cart", "0001_initial")])
        estado_anterior = executor.loader.project_state([("cart", "0001_initial")])
        CarritoAnterior = estado_anterior.apps.get_model("cart", "Carrito")
        CarritoAnterior.objects.create(session_key="migracion-a")
        CarritoAnterior.objects.create(session_key="migracion-b")

        executor = MigrationExecutor(connection)
        executor.migrate([("cart", "0002_token_checkout")])
        estado_nuevo = executor.loader.project_state([("cart", "0002_token_checkout")])
        CarritoNuevo = estado_nuevo.apps.get_model("cart", "Carrito")
        tokens = list(
            CarritoNuevo.objects.order_by("pk").values_list(
                "token_checkout", flat=True
            )
        )
        self.assertEqual(len(tokens), 2)
        self.assertTrue(all(tokens))
        self.assertEqual(len(set(tokens)), 2)

        nuevo = CarritoNuevo.objects.create(session_key="migracion-default")
        self.assertIsNotNone(nuevo.token_checkout)
        self.assertNotIn(nuevo.token_checkout, tokens)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CarritoNuevo.objects.create(
                session_key="migracion-duplicada",
                token_checkout=nuevo.token_checkout,
            )


class MigracionMovimientosPedidoTests(PruebaMigracionRestaurable):
    def _migrar_a_estado_anterior(self):
        executor = MigrationExecutor(connection)
        executor.migrate(
            [
                ("inventory", "0002_movimientoinventario"),
                ("orders", "0001_initial"),
            ]
        )
        return executor.loader.project_state(
            [("inventory", "0002_movimientoinventario")]
        )

    def _crear_inventario_historico(self, estado, nombre):
        Producto = estado.apps.get_model("catalog", "Producto")
        Inventario = estado.apps.get_model("inventory", "Inventario")
        producto = Producto.objects.create(
            nombre=nombre,
            descripcion="",
            peso="1 kg",
            imagen="productos/historico.gif",
            precio_unitario="1.00",
            precio_desde_3="1.00",
            precio_desde_20="1.00",
            activo=True,
        )
        return Inventario.objects.create(
            producto=producto,
            cantidad_disponible=5,
        )

    def test_movimientos_administrativos_sobreviven_con_pedido_nulo(self):
        estado = self._migrar_a_estado_anterior()
        MovimientoAnterior = estado.apps.get_model(
            "inventory", "MovimientoInventario"
        )
        inventario = self._crear_inventario_historico(
            estado, "Histórico administrativo"
        )
        movimiento = MovimientoAnterior.objects.create(
            inventario=inventario,
            tipo_movimiento="INGRESO_MERCADERIA",
            cantidad=5,
            observacion="Ingreso previo",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(MIGRACION_FINAL)
        estado_final = executor.loader.project_state(MIGRACION_FINAL)
        MovimientoFinal = estado_final.apps.get_model(
            "inventory", "MovimientoInventario"
        )
        migrado = MovimientoFinal.objects.get(pk=movimiento.pk)
        self.assertIsNone(migrado.pedido_id)
        self.assertEqual(migrado.tipo_movimiento, "INGRESO_MERCADERIA")
        self.assertEqual(migrado.cantidad, 5)

    def test_movimiento_reservado_historico_aborta_con_ids(self):
        estado = self._migrar_a_estado_anterior()
        Movimiento = estado.apps.get_model("inventory", "MovimientoInventario")
        inventario = self._crear_inventario_historico(
            estado, "Histórico incompatible"
        )
        movimiento = Movimiento.objects.create(
            inventario=inventario,
            tipo_movimiento="VENTA_PEDIDO",
            cantidad=1,
            observacion="",
        )
        self.ids_incompatibles.append(movimiento.pk)

        with self.assertRaisesRegex(RuntimeError, str(movimiento.pk)):
            MigrationExecutor(connection).migrate(MIGRACION_FINAL)
