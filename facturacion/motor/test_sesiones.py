"""Tests del motor de sesiones.

Los casos de 'enero 2026' no son inventados: son los numeros que realmente
tiene la planilla, verificados contra el calendario. Enero 2026 tiene 22 dias
habiles y el 1 (jueves) es feriado, asi que un paciente con los 5 dias
autorizados da 21 sesiones, que es exactamente lo que factura la planilla.
"""

import unittest
from datetime import date

from modelo import (
    Autorizacion,
    AutorizacionDia,
    CondicionIVA,
    ConvenioApross,
    Feriado,
    Internacion,
    MotivoNoFacturable,
    ObraSocial,
    ValorModulo,
)
from sesiones import (
    ValorModuloFaltante,
    buscar_valor,
    calcular_monto,
    generar_anio,
    generar_sesiones,
    tope_efectivo,
)

ANIO_NUEVO = [Feriado(date(2026, 1, 1), "Año Nuevo")]


def obra_social(tiene_tope=False, tope=None):
    return ObraSocial(id=1, nombre="SANCOR", cuit="30590354798",
                      condicion_iva=CondicionIVA.EXENTO,
                      tiene_tope=tiene_tope, tope_sesiones_mes=tope)


def autorizacion(dias, desde=date(2026, 1, 1), hasta=date(2026, 12, 31), tope=None):
    return Autorizacion(
        id=1, paciente_id=1, vigencia_desde=desde, vigencia_hasta=hasta,
        dias=[AutorizacionDia(d, "15:00") for d in dias], tope_sesiones_mes=tope)


class TestCalendarioReal(unittest.TestCase):
    """Contra los numeros que tiene la planilla en enero 2026."""

    def test_cinco_dias_con_el_feriado_de_anio_nuevo_da_21(self):
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(), feriados=ANIO_NUEVO)
        self.assertEqual(r.cantidad, 21)
        self.assertEqual(len(r.descartadas_por(MotivoNoFacturable.FERIADO)), 1)

    def test_sin_el_feriado_serian_22(self):
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1, obra_social())
        self.assertEqual(r.cantidad, 22)

    def test_cuatro_dias_da_16_o_17_segun_si_incluyen_el_jueves(self):
        # Enero 2026 tiene 4 lunes, 4 martes, 4 miercoles, 5 jueves y 5 viernes,
        # y el feriado cae jueves. Los 17 de la planilla salen de una
        # combinacion que no incluye el jueves.
        con_jueves = generar_sesiones(autorizacion([1, 2, 3, 4]), 2026, 1,
                                      obra_social(), feriados=ANIO_NUEVO)
        self.assertEqual(con_jueves.cantidad, 16)      # 17 - 1 feriado
        sin_jueves = generar_sesiones(autorizacion([1, 2, 3, 5]), 2026, 1,
                                      obra_social(), feriados=ANIO_NUEVO)
        self.assertEqual(sin_jueves.cantidad, 17)      # el feriado no le pega

    def test_tres_dias_da_12_o_13_segun_cuales(self):
        lun_mie_vie = generar_sesiones(autorizacion([1, 3, 5]), 2026, 1,
                                       obra_social(), feriados=ANIO_NUEVO)
        self.assertEqual(lun_mie_vie.cantidad, 13)
        lun_mar_mie = generar_sesiones(autorizacion([1, 2, 3]), 2026, 1,
                                       obra_social(), feriados=ANIO_NUEVO)
        self.assertEqual(lun_mar_mie.cantidad, 12)

    def test_dos_dias_da_8_o_9(self):
        lun_mar = generar_sesiones(autorizacion([1, 2]), 2026, 1,
                                   obra_social(), feriados=ANIO_NUEVO)
        self.assertEqual(lun_mar.cantidad, 8)
        jue_vie = generar_sesiones(autorizacion([4, 5]), 2026, 1,
                                   obra_social(), feriados=ANIO_NUEVO)
        self.assertEqual(jue_vie.cantidad, 9)   # 5 jueves - 1 feriado + 5 viernes

    def test_nunca_genera_sabados_ni_domingos(self):
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1, obra_social())
        self.assertTrue(all(s.fecha.weekday() < 5 for s in r.sesiones))


class TestHorarioPorDia(unittest.TestCase):
    def test_cada_dia_lleva_su_propio_horario(self):
        aut = Autorizacion(
            id=1, paciente_id=1, vigencia_desde=date(2026, 1, 1),
            dias=[AutorizacionDia(1, "15:00"), AutorizacionDia(3, "16:30")])
        r = generar_sesiones(aut, 2026, 1, obra_social())
        lunes = [s for s in r.sesiones if s.fecha.weekday() == 0]
        miercoles = [s for s in r.sesiones if s.fecha.weekday() == 2]
        self.assertTrue(all(s.hora == "15:00" for s in lunes))
        self.assertTrue(all(s.hora == "16:30" for s in miercoles))

    def test_no_acepta_dias_repetidos(self):
        with self.assertRaises(ValueError):
            Autorizacion(id=1, paciente_id=1, vigencia_desde=date(2026, 1, 1),
                         dias=[AutorizacionDia(1), AutorizacionDia(1)])

    def test_no_acepta_sabado(self):
        with self.assertRaises(ValueError):
            AutorizacionDia(6)


class TestInternacion(unittest.TestCase):
    def test_los_dias_internado_no_se_facturan(self):
        interna = Internacion(paciente_id=1, desde=date(2026, 1, 5),
                              hasta=date(2026, 1, 16), institucion="Sanatorio")
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(), feriados=ANIO_NUEVO,
                             internaciones=[interna])
        caidas = r.descartadas_por(MotivoNoFacturable.INTERNACION)
        self.assertEqual(len(caidas), 10)        # dos semanas habiles
        self.assertEqual(r.cantidad, 11)         # 21 - 10

    def test_internacion_abierta_alcanza_todo_lo_que_sigue(self):
        interna = Internacion(paciente_id=1, desde=date(2026, 1, 20), hasta=None)
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(), internaciones=[interna])
        self.assertEqual(len(r.descartadas_por(MotivoNoFacturable.INTERNACION)), 9)

    def test_la_internacion_de_otro_paciente_no_afecta(self):
        ajena = Internacion(paciente_id=99, desde=date(2026, 1, 1),
                            hasta=date(2026, 1, 31))
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(), internaciones=[ajena])
        self.assertEqual(r.cantidad, 22)

    def test_feriado_e_internacion_el_mismo_dia_no_descuentan_dos_veces(self):
        interna = Internacion(paciente_id=1, desde=date(2026, 1, 1),
                              hasta=date(2026, 1, 2))
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(), feriados=ANIO_NUEVO,
                             internaciones=[interna])
        # 1-ene es feriado y tambien cae internado: se cuenta una sola vez
        self.assertEqual(r.cantidad, 20)
        self.assertEqual(len(r.descartadas), 2)


class TestTopes(unittest.TestCase):
    def test_sin_tope_se_factura_el_calendario_completo(self):
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(), feriados=ANIO_NUEVO)
        self.assertIsNone(r.tope_aplicado)
        self.assertEqual(r.cantidad, 21)

    def test_el_tope_de_la_obra_social_recorta_a_20(self):
        # Es lo que se ve en OSSACRA y OSPE: 5 dias autorizados pero facturan 20
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(tiene_tope=True, tope=20),
                             feriados=ANIO_NUEVO)
        self.assertEqual(r.cantidad, 20)
        self.assertEqual(len(r.descartadas_por(MotivoNoFacturable.FUERA_DE_TOPE)), 1)

    def test_precedencia_autorizacion_gana_sobre_modulo_y_obra_social(self):
        valor = ValorModulo("APE", 2026, 1, 16753.78, tope_sesiones=18)
        tope = tope_efectivo(autorizacion([1, 2, 3, 4, 5], tope=10), valor,
                             obra_social(tiene_tope=True, tope=20))
        self.assertEqual(tope, 10)

    def test_precedencia_modulo_gana_sobre_obra_social(self):
        valor = ValorModulo("OSPIA", 2026, 1, 16753.78, tope_sesiones=18)
        tope = tope_efectivo(autorizacion([1, 2, 3, 4, 5]), valor,
                             obra_social(tiene_tope=True, tope=20))
        self.assertEqual(tope, 18)

    def test_obra_social_sin_tope_no_topea_aunque_tenga_numero_viejo(self):
        os_ = ObraSocial(id=1, nombre="SANCOR", tiene_tope=False,
                         tope_sesiones_mes=16)
        self.assertIsNone(tope_efectivo(autorizacion([1, 2, 3, 4, 5]), None, os_))

    def test_tiene_tope_sin_valor_es_un_error_de_configuracion(self):
        with self.assertRaises(ValueError):
            ObraSocial(id=1, nombre="X", tiene_tope=True, tope_sesiones_mes=None)

    def test_el_tope_descarta_las_ultimas_del_mes(self):
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(tiene_tope=True, tope=20),
                             feriados=ANIO_NUEVO)
        fuera = r.descartadas_por(MotivoNoFacturable.FUERA_DE_TOPE)
        self.assertEqual(fuera[0].fecha, date(2026, 1, 30))


class TestVigencia(unittest.TestCase):
    def test_antes_de_la_vigencia_no_se_factura(self):
        aut = autorizacion([1, 2, 3, 4, 5], desde=date(2026, 1, 15))
        r = generar_sesiones(aut, 2026, 1, obra_social())
        self.assertTrue(
            all(s.motivo_no_facturable == MotivoNoFacturable.FUERA_DE_VIGENCIA
                for s in r.sesiones if s.fecha < date(2026, 1, 15)))

    def test_autorizacion_sin_fecha_de_fin_sigue_vigente(self):
        aut = Autorizacion(id=1, paciente_id=1, vigencia_desde=date(2026, 1, 1),
                           vigencia_hasta=None,
                           dias=[AutorizacionDia(1)])
        r = generar_sesiones(aut, 2026, 12, obra_social())
        self.assertEqual(r.cantidad, 4)

    def test_vigencia_invertida_es_un_error(self):
        with self.assertRaises(ValueError):
            autorizacion([1], desde=date(2026, 6, 1), hasta=date(2026, 1, 1))


class TestMonto(unittest.TestCase):
    VALORES = [
        ValorModulo("APE", 2026, 1, 16753.78),
        ValorModulo("CAMI", 2026, 1, 10052.27),
    ]

    def test_reproduce_los_montos_de_la_planilla(self):
        ape = buscar_valor(self.VALORES, "APE", 2026, 1)
        self.assertEqual(calcular_monto(17, ape), 284814.26)
        self.assertEqual(calcular_monto(21, ape), 351829.38)
        self.assertEqual(calcular_monto(12, ape), 201045.36)

    def test_reproduce_los_montos_de_cami(self):
        cami = buscar_valor(self.VALORES, "CAMI", 2026, 1)
        self.assertEqual(calcular_monto(16, cami), 160836.32)
        self.assertEqual(calcular_monto(20, cami), 201045.40)

    def test_si_falta_el_valor_del_mes_corta_y_no_arrastra(self):
        with self.assertRaises(ValorModuloFaltante):
            buscar_valor(self.VALORES, "APE", 2026, 3)

    def test_el_mensaje_dice_donde_cargarlo(self):
        with self.assertRaises(ValorModuloFaltante) as e:
            buscar_valor(self.VALORES, "OSPEDYC", 2026, 5)
        self.assertIn("OSPEDYC", str(e.exception))
        self.assertIn("05/2026", str(e.exception))


class TestExplicacion(unittest.TestCase):
    def test_la_explicacion_responde_por_que_ese_numero(self):
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(), feriados=ANIO_NUEVO)
        texto = r.explicacion()
        self.assertIn("21 sesiones", texto)
        self.assertIn("feriado", texto)
        self.assertIn("01/01", texto)

    def test_transcurridas_depende_de_la_fecha_en_que_se_mire(self):
        r = generar_sesiones(autorizacion([1, 2, 3, 4, 5]), 2026, 1,
                             obra_social(), feriados=ANIO_NUEVO)
        # habiles hasta el 15 inclusive: 2, 5-9 y 12-15; el 1 es feriado
        self.assertEqual(r.transcurridas(date(2026, 1, 15)), 10)
        self.assertEqual(r.transcurridas(date(2026, 1, 31)), 21)
        self.assertEqual(r.transcurridas(date(2025, 12, 31)), 0)


class TestAnioCompleto(unittest.TestCase):
    def test_genera_los_doce_meses(self):
        anio = generar_anio(autorizacion([1, 2, 3, 4, 5]), 2026, obra_social(),
                            feriados=ANIO_NUEVO)
        self.assertEqual(sorted(anio), list(range(1, 13)))

    def test_el_total_anual_descuenta_los_feriados_cargados(self):
        feriados = [Feriado(date(2026, 1, 1), "Año Nuevo"),
                    Feriado(date(2026, 5, 1), "Día del Trabajador")]
        anio = generar_anio(autorizacion([1, 2, 3, 4, 5]), 2026, obra_social(),
                            feriados=feriados)
        habiles_2026 = sum(
            1 for mes in range(1, 13)
            for s in generar_anio(autorizacion([1, 2, 3, 4, 5]), 2026,
                                  obra_social())[mes].facturables)
        self.assertEqual(sum(r.cantidad for r in anio.values()), habiles_2026 - 2)


class TestApross(unittest.TestCase):
    def test_reproduce_el_cierre_de_la_planilla(self):
        # TOTAL 6.031.260 -> Desc 4% 241.250,40 -> Imp. Abonado 5.790.009,60
        c = ConvenioApross(anio=2026, mes=1, cantidad_pacientes=31,
                           cantidad_sesiones=0, valor_sesion=0,
                           total_bruto_manual=6031260)
        self.assertEqual(c.total_bruto, 6031260)
        self.assertEqual(c.monto_descuento, 241250.40)
        self.assertEqual(c.importe_abonado, 5790009.60)

    def test_el_total_tambien_sale_de_sesiones_por_valor(self):
        c = ConvenioApross(anio=2026, mes=3, cantidad_pacientes=31,
                           cantidad_sesiones=372, valor_sesion=16753.78)
        self.assertEqual(c.total_bruto, 6232406.16)
        self.assertEqual(c.importe_abonado,
                         round(6232406.16 - 6232406.16 * 0.04, 2))

    def test_el_porcentaje_es_configurable(self):
        c = ConvenioApross(anio=2026, mes=1, cantidad_pacientes=31,
                           cantidad_sesiones=100, valor_sesion=1000,
                           porcentaje_descuento=10)
        self.assertEqual(c.monto_descuento, 10000)
        self.assertEqual(c.importe_abonado, 90000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
