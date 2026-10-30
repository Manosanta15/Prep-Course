"""Tests de la etapa 4: facturas, pagos, semaforo y confirmaciones.

Los montos son los reales de enero 2026: 17 sesiones a 16.753,78 dan
284.814,26, que es lo que dice la planilla.
"""

import unittest
from datetime import date

import cobranza
import confirmaciones
from facturacion import (
    OperacionNoPermitida,
    anular,
    emitir,
    esta_cobrada,
    preparar_factura,
    reemplazar,
    registrar_pago,
    revertir_pago,
)
from modelo import (
    Autorizacion,
    AutorizacionDia,
    EstadoCobranza,
    EstadoFactura,
    Factura,
    Feriado,
    MedioPago,
    ObraSocial,
    Paciente,
    Pago,
    ValorModulo,
)
from sesiones import generar_sesiones

SANCOR = ObraSocial(id=1, nombre="SANCOR", cuit="30590354798", dias_vencimiento=60)
OSES = {1: SANCOR}
VALOR_ENERO = ValorModulo("APE", 2026, 1, 16753.78)
EMISION = date(2026, 2, 10)


def paciente():
    return Paciente(id=1, apellido_nombre="Paciente ejemplo 1", obra_social_id=1,
                    modulo_codigo="APE", profesional_id=7)


def factura_emitida(monto=284814.26, sesiones=17, fecha=EMISION, id_=1, mes=1):
    f = Factura(id=id_, paciente_id=1, obra_social_id=1, profesional_id=7,
                anio=2026, mes=mes, cantidad_sesiones=sesiones,
                valor_sesion=16753.78, monto=monto)
    return emitir(f, "661", fecha)


class TestEmision(unittest.TestCase):
    def test_arma_la_factura_desde_las_sesiones_generadas(self):
        aut = Autorizacion(id=1, paciente_id=1, vigencia_desde=date(2026, 1, 1),
                           dias=[AutorizacionDia(d) for d in (1, 2, 3, 4, 5)])
        r = generar_sesiones(aut, 2026, 1, SANCOR,
                             feriados=[Feriado(date(2026, 1, 1), "Año Nuevo")])
        f = preparar_factura(1, paciente(), SANCOR, r, VALOR_ENERO)
        self.assertEqual(f.cantidad_sesiones, 21)
        self.assertEqual(f.monto, 351829.38)        # el monto real de la planilla
        self.assertEqual(f.estado, EstadoFactura.BORRADOR)
        self.assertEqual(f.profesional_id, 7)

    def test_no_mezcla_el_valor_de_otro_mes(self):
        aut = Autorizacion(id=1, paciente_id=1, vigencia_desde=date(2026, 1, 1),
                           dias=[AutorizacionDia(1)])
        r = generar_sesiones(aut, 2026, 3, SANCOR)
        with self.assertRaises(OperacionNoPermitida):
            preparar_factura(1, paciente(), SANCOR, r, VALOR_ENERO)

    def test_emitir_exige_numero(self):
        f = Factura(id=1, paciente_id=1, obra_social_id=1, anio=2026, mes=1,
                    cantidad_sesiones=17, valor_sesion=16753.78, monto=284814.26)
        with self.assertRaises(OperacionNoPermitida):
            emitir(f, "   ", EMISION)

    def test_no_emite_un_mes_sin_sesiones(self):
        f = Factura(id=1, paciente_id=1, obra_social_id=1, anio=2026, mes=1,
                    cantidad_sesiones=0, valor_sesion=16753.78, monto=0.0)
        with self.assertRaises(OperacionNoPermitida):
            emitir(f, "999", EMISION)

    def test_una_factura_emitida_no_se_reemite(self):
        f = factura_emitida()
        with self.assertRaises(OperacionNoPermitida) as e:
            emitir(f, "662", EMISION)
        self.assertIn("anularla", str(e.exception))

    def test_el_valor_queda_congelado_al_emitir(self):
        f = factura_emitida()
        # Aunque el modulo cambie despues, la factura sigue diciendo lo mismo.
        self.assertEqual(f.valor_sesion, 16753.78)
        self.assertEqual(f.monto, 284814.26)


class TestSemaforo(unittest.TestCase):
    def test_borrador_es_gris(self):
        f = Factura(id=1, paciente_id=1, obra_social_id=1, anio=2026, mes=1,
                    cantidad_sesiones=17, valor_sesion=16753.78, monto=284814.26)
        est = cobranza.estado_cobranza(f, [], SANCOR, date(2026, 2, 1))
        self.assertEqual(est, EstadoCobranza.SIN_FACTURAR)
        self.assertEqual(cobranza.COLOR[est], "gris")

    def test_sin_pago_y_reciente_es_amarillo(self):
        f = factura_emitida()
        est = cobranza.estado_cobranza(f, [], SANCOR, date(2026, 3, 1))
        self.assertEqual(est, EstadoCobranza.PENDIENTE)
        self.assertEqual(cobranza.COLOR[est], "amarillo")

    def test_a_los_59_dias_sigue_amarillo(self):
        # El ultimo dia en amarillo: emitida el 10/02, el dia 59 es el 10/04.
        f = factura_emitida()
        hoy = date(2026, 4, 10)
        self.assertEqual(cobranza.dias_desde_emision(f, hoy), 59)
        self.assertEqual(cobranza.estado_cobranza(f, [], SANCOR, hoy),
                         EstadoCobranza.PENDIENTE)

    def test_a_los_60_dias_pasa_a_rojo(self):
        f = factura_emitida()
        hoy = date(2026, 4, 11)
        self.assertEqual(cobranza.dias_desde_emision(f, hoy), 60)
        self.assertEqual(cobranza.estado_cobranza(f, [], SANCOR, hoy),
                         EstadoCobranza.ATRASADO)

    def test_los_dias_de_vencimiento_salen_de_la_obra_social(self):
        f = factura_emitida()
        rapida = ObraSocial(id=1, nombre="X", dias_vencimiento=30)
        hoy = date(2026, 3, 15)     # 33 dias
        self.assertEqual(cobranza.estado_cobranza(f, [], rapida, hoy),
                         EstadoCobranza.ATRASADO)
        self.assertEqual(cobranza.estado_cobranza(f, [], SANCOR, hoy),
                         EstadoCobranza.PENDIENTE)

    def test_pago_completo_es_verde(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6))
        self.assertEqual(p.monto, 284814.26)
        est = cobranza.estado_cobranza(f, [p], SANCOR, date(2026, 5, 1))
        self.assertEqual(est, EstadoCobranza.COBRADO)
        self.assertEqual(cobranza.COLOR[est], "verde")

    def test_pago_parcial_es_azul(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6), monto=200000)
        est = cobranza.estado_cobranza(f, [p], SANCOR, date(2026, 3, 10))
        self.assertEqual(est, EstadoCobranza.PARCIAL)
        self.assertEqual(cobranza.COLOR[est], "azul")
        self.assertEqual(cobranza.saldo(f, [p]), 84814.26)

    def test_el_azul_gana_sobre_el_rojo_aunque_este_vencida(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6), monto=200000)
        est = cobranza.estado_cobranza(f, [p], SANCOR, date(2026, 8, 1))
        self.assertEqual(est, EstadoCobranza.PARCIAL)

    def test_varios_pagos_que_suman_el_total_dan_verde(self):
        f = factura_emitida()
        p1 = registrar_pago(1, f, [], date(2026, 3, 6), monto=100000)
        p2 = registrar_pago(2, f, [p1], date(2026, 4, 6), monto=100000)
        p3 = registrar_pago(3, f, [p1, p2], date(2026, 5, 6))
        self.assertEqual(p3.monto, 84814.26)
        self.assertTrue(esta_cobrada(f, [p1, p2, p3]))
        self.assertEqual(cobranza.estado_cobranza(f, [p1, p2, p3], SANCOR,
                                                  date(2026, 6, 1)),
                         EstadoCobranza.COBRADO)

    def test_la_leyenda_de_la_celda_explica_el_color(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6), monto=200000)
        self.assertIn("saldo", cobranza.fila(f, [p], SANCOR, date(2026, 4, 1)).leyenda)
        self.assertIn("atrasado",
                      cobranza.fila(f, [], SANCOR, date(2026, 5, 1)).leyenda)


class TestPagos(unittest.TestCase):
    def test_el_monto_por_defecto_es_el_saldo(self):
        f = factura_emitida()
        p1 = registrar_pago(1, f, [], date(2026, 3, 6), monto=84814.26)
        p2 = registrar_pago(2, f, [p1], date(2026, 4, 6))
        self.assertEqual(p2.monto, 200000.0)

    def test_no_acepta_un_pago_mayor_al_saldo_sin_confirmacion(self):
        f = factura_emitida()
        with self.assertRaises(OperacionNoPermitida) as e:
            registrar_pago(1, f, [], date(2026, 3, 6), monto=300000)
        self.assertIn("supera el saldo", str(e.exception))

    def test_el_exceso_se_puede_confirmar_explicitamente(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6), monto=300000,
                           permitir_exceso=True)
        self.assertEqual(p.monto, 300000)

    def test_no_se_registra_un_pago_de_un_borrador(self):
        f = Factura(id=1, paciente_id=1, obra_social_id=1, anio=2026, mes=1,
                    cantidad_sesiones=17, valor_sesion=16753.78, monto=284814.26)
        with self.assertRaises(OperacionNoPermitida):
            registrar_pago(1, f, [], date(2026, 3, 6))

    def test_revertir_deja_el_original_y_suma_cero(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6))
        rev = revertir_pago(2, p, date(2026, 3, 20), "el deposito no se acredito")
        self.assertEqual(rev.monto, -284814.26)
        self.assertEqual(rev.revierte_a_pago_id, 1)
        self.assertTrue(rev.es_reversion)
        self.assertEqual(cobranza.cobrado(f, [p, rev]), 0.0)
        # Y la factura vuelve al color que le corresponde por antiguedad
        self.assertEqual(cobranza.estado_cobranza(f, [p, rev], SANCOR,
                                                  date(2026, 6, 1)),
                         EstadoCobranza.ATRASADO)

    def test_no_se_revierte_una_reversion(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6))
        rev = revertir_pago(2, p, date(2026, 3, 20), "error")
        with self.assertRaises(OperacionNoPermitida):
            revertir_pago(3, rev, date(2026, 3, 21), "otro")

    def test_la_reversion_necesita_motivo(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6))
        with self.assertRaises(OperacionNoPermitida):
            revertir_pago(2, p, date(2026, 3, 20), "  ")


class TestAnulacion(unittest.TestCase):
    def test_anular_revierte_los_pagos(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6))
        res = anular(f, [p], "sesiones mal contadas", date(2026, 3, 25),
                     proximo_id_pago=2)
        self.assertEqual(f.estado, EstadoFactura.ANULADA)
        self.assertEqual(len(res.reversiones), 1)
        self.assertEqual(cobranza.cobrado(f, [p] + res.reversiones), 0.0)

    def test_una_anulada_no_aparece_en_el_tablero(self):
        f = factura_emitida()
        anular(f, [], "error", date(2026, 3, 25))
        t = cobranza.tablero_anual([f], [], OSES, 2026, date(2026, 4, 1))
        self.assertIsNone(t.celda(1, 1))
        self.assertEqual(t.total_anual, 0.0)

    def test_solo_se_anulan_las_emitidas(self):
        f = Factura(id=1, paciente_id=1, obra_social_id=1, anio=2026, mes=1,
                    cantidad_sesiones=17, valor_sesion=16753.78, monto=284814.26)
        with self.assertRaises(OperacionNoPermitida):
            anular(f, [], "motivo", date(2026, 3, 25))

    def test_la_anulacion_necesita_motivo(self):
        f = factura_emitida()
        with self.assertRaises(OperacionNoPermitida):
            anular(f, [], "", date(2026, 3, 25))

    def test_el_reemplazo_apunta_a_la_anulada(self):
        vieja = factura_emitida()
        anular(vieja, [], "sesiones mal contadas", date(2026, 3, 25))
        nueva = reemplazar(2, vieja)
        self.assertEqual(nueva.anula_a_factura_id, 1)
        self.assertEqual(nueva.estado, EstadoFactura.BORRADOR)
        self.assertEqual(nueva.mes, vieja.mes)

    def test_no_se_reemplaza_una_factura_vigente(self):
        f = factura_emitida()
        with self.assertRaises(OperacionNoPermitida):
            reemplazar(2, f)


class TestCuadroDeMontos(unittest.TestCase):
    def armar(self):
        cobrada = factura_emitida(id_=1, mes=1)
        pago = registrar_pago(1, cobrada, [], date(2026, 3, 6))
        parcial = factura_emitida(id_=2, mes=2, monto=100000, sesiones=6,
                                  fecha=date(2026, 3, 10))
        pago_parcial = registrar_pago(2, parcial, [], date(2026, 3, 20), monto=40000)
        pendiente = factura_emitida(id_=3, mes=3, monto=50000, sesiones=3,
                                    fecha=date(2026, 4, 20))
        atrasada = factura_emitida(id_=4, mes=4, monto=70000, sesiones=4,
                                   fecha=date(2026, 1, 15))
        return ([cobrada, parcial, pendiente, atrasada], [pago, pago_parcial])

    def test_los_totales_del_cuadro(self):
        facturas, pagos = self.armar()
        r = cobranza.resumen(facturas, pagos, OSES, date(2026, 5, 1))
        self.assertEqual(r.facturado, round(284814.26 + 100000 + 50000 + 70000, 2))
        self.assertEqual(r.cobrado, round(284814.26 + 40000, 2))
        self.assertEqual(r.parcial_pendiente, 60000.0)
        self.assertEqual(r.pendiente, 50000.0)
        self.assertEqual(r.atrasado, 70000.0)

    def test_facturado_menos_cobrado_es_lo_que_falta_entrar(self):
        facturas, pagos = self.armar()
        r = cobranza.resumen(facturas, pagos, OSES, date(2026, 5, 1))
        self.assertAlmostEqual(r.por_cobrar, r.facturado - r.cobrado, places=2)

    def test_cuenta_cuantas_hay_de_cada_color(self):
        facturas, pagos = self.armar()
        r = cobranza.resumen(facturas, pagos, OSES, date(2026, 5, 1))
        self.assertEqual(r.cantidad_por_estado[EstadoCobranza.COBRADO], 1)
        self.assertEqual(r.cantidad_por_estado[EstadoCobranza.PARCIAL], 1)
        self.assertEqual(r.cantidad_por_estado[EstadoCobranza.PENDIENTE], 1)
        self.assertEqual(r.cantidad_por_estado[EstadoCobranza.ATRASADO], 1)


class TestTableroAnual(unittest.TestCase):
    def test_totales_por_paciente_por_mes_y_por_profesional(self):
        f1 = factura_emitida(id_=1, mes=1)
        f2 = factura_emitida(id_=2, mes=2, monto=351829.38, sesiones=21)
        otro = Factura(id=3, paciente_id=2, obra_social_id=1, profesional_id=9,
                       anio=2026, mes=1, cantidad_sesiones=12,
                       valor_sesion=16753.78, monto=201045.36)
        emitir(otro, "664", EMISION)

        t = cobranza.tablero_anual([f1, f2, otro], [], OSES, 2026, date(2026, 3, 1))
        self.assertEqual(t.total_por_paciente[1], round(284814.26 + 351829.38, 2))
        self.assertEqual(t.total_por_paciente[2], 201045.36)
        self.assertEqual(t.total_por_mes[1], round(284814.26 + 201045.36, 2))
        self.assertEqual(t.total_por_mes[2], 351829.38)
        self.assertEqual(t.total_por_profesional[7], round(284814.26 + 351829.38, 2))
        self.assertEqual(t.total_por_profesional[9], 201045.36)

    def test_el_color_de_cada_celda(self):
        cobrada = factura_emitida(id_=1, mes=1)
        pago = registrar_pago(1, cobrada, [], date(2026, 3, 6))
        atrasada = factura_emitida(id_=2, mes=2, monto=70000, sesiones=4,
                                   fecha=date(2026, 1, 15))
        t = cobranza.tablero_anual([cobrada, atrasada], [pago], OSES, 2026,
                                   date(2026, 5, 1))
        self.assertEqual(t.color(1, 1), "verde")
        self.assertEqual(t.color(1, 2), "rojo")
        self.assertEqual(t.color(1, 7), "gris")       # mes sin facturar

    def test_ignora_las_facturas_de_otro_anio(self):
        f = Factura(id=1, paciente_id=1, obra_social_id=1, anio=2025, mes=12,
                    cantidad_sesiones=10, valor_sesion=1000, monto=10000)
        emitir(f, "1", date(2026, 1, 5))
        t = cobranza.tablero_anual([f], [], OSES, 2026, date(2026, 3, 1))
        self.assertEqual(t.total_anual, 0.0)


class TestConfirmaciones(unittest.TestCase):
    def test_cambiar_el_valor_dice_cuantas_recalcula_y_cuantas_no(self):
        borrador = Factura(id=1, paciente_id=1, obra_social_id=1, anio=2026, mes=1,
                           cantidad_sesiones=17, valor_sesion=16753.78,
                           monto=284814.26)
        emitida = factura_emitida(id_=2, mes=1)
        c = confirmaciones.cambiar_valor_modulo(VALOR_ENERO, 18500.0,
                                                [borrador, emitida])
        self.assertEqual(c.nivel, confirmaciones.Nivel.CONFIRMAR_ANTES)
        self.assertIn("1 facturacion sin emitir", c.mensaje)
        self.assertIn("no se modifica", c.mensaje)
        self.assertIn("18,500.00", c.mensaje)

    def test_la_primera_carga_de_un_valor_no_recalcula_nada(self):
        c = confirmaciones.cambiar_valor_modulo(None, 16753.78, [])
        self.assertIn("primera carga", c.mensaje)

    def test_un_feriado_de_fin_de_semana_no_molesta_al_usuario(self):
        c = confirmaciones.agregar_feriado(
            Feriado(date(2026, 6, 20), "Belgrano"), [], [])
        self.assertEqual(c.nivel, confirmaciones.Nivel.AVISAR_DESPUES)
        self.assertIn("no afecta", c.mensaje)

    def test_un_feriado_habil_dice_a_cuantos_pacientes_alcanza(self):
        auts = [Autorizacion(id=i, paciente_id=i, vigencia_desde=date(2026, 1, 1),
                             vigencia_hasta=date(2026, 12, 31),
                             dias=[AutorizacionDia(5)])          # viernes
                for i in range(1, 15)]
        c = confirmaciones.agregar_feriado(
            Feriado(date(2026, 5, 1), "Día del Trabajador"), auts, [])
        self.assertEqual(c.nivel, confirmaciones.Nivel.CONFIRMAR_ANTES)
        self.assertIn("14 pacientes", c.mensaje)
        self.assertIn("quitar 14 sesiones", c.mensaje)

    def test_el_feriado_avisa_si_el_mes_ya_tiene_facturas_emitidas(self):
        aut = [Autorizacion(id=1, paciente_id=1, vigencia_desde=date(2026, 1, 1),
                            vigencia_hasta=date(2026, 12, 31),
                            dias=[AutorizacionDia(5)])]
        emitida = factura_emitida(id_=1, mes=5)
        c = confirmaciones.agregar_feriado(
            Feriado(date(2026, 5, 1), "Día del Trabajador"), aut, [emitida])
        self.assertIn("anularlas y re-emitirlas", c.mensaje)

    def test_anular_pide_escribir_el_numero_de_factura(self):
        f = factura_emitida()
        p = registrar_pago(1, f, [], date(2026, 3, 6))
        c = confirmaciones.anular_factura(f, [p], "Paciente ejemplo 1")
        self.assertEqual(c.nivel, confirmaciones.Nivel.ESCRIBIR_PARA_CONFIRMAR)
        self.assertEqual(c.texto_a_escribir, "661")
        self.assertIn("revierte el pago", c.mensaje)
        self.assertIn("06/03/2026", c.mensaje)

    def test_dar_de_baja_un_paciente_con_facturas_es_nivel_3(self):
        c = confirmaciones.baja_paciente(1, "Paciente ejemplo 1",
                                         [factura_emitida()])
        self.assertEqual(c.nivel, confirmaciones.Nivel.ESCRIBIR_PARA_CONFIRMAR)
        self.assertIn("el sistema no borra", c.mensaje)

    def test_dar_de_baja_uno_sin_movimiento_es_nivel_2(self):
        c = confirmaciones.baja_paciente(99, "Nadie", [factura_emitida()])
        self.assertEqual(c.nivel, confirmaciones.Nivel.CONFIRMAR_ANTES)

    def test_cerrar_apross_pide_escribir_el_periodo(self):
        c = confirmaciones.cerrar_convenio_apross(
            2026, 1, 6031260.0, 241250.40, 5790009.60)
        self.assertEqual(c.texto_a_escribir, "01/2026")
        self.assertIn("5,790,009.60", c.mensaje)

    def test_un_cambio_menor_se_guarda_y_avisa_despues(self):
        c = confirmaciones.cambio_menor("Horario del miercoles")
        self.assertEqual(c.nivel, confirmaciones.Nivel.AVISAR_DESPUES)
        self.assertIn("Deshacer", c.mensaje)


if __name__ == "__main__":
    unittest.main(verbosity=2)
