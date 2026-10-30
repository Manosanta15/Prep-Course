"""Tests de la base y la capa de servicios: el ciclo completo en memoria."""

import unittest
from datetime import date

import persistencia as db
import servicios
from facturacion import OperacionNoPermitida
from modelo import (
    Autorizacion,
    AutorizacionDia,
    CondicionIVA,
    EstadoCobranza,
    EstadoFactura,
    Feriado,
    Internacion,
    Modulo,
    MotivoNoFacturable,
    ObraSocial,
    Paciente,
    Profesional,
    ValorModulo,
)
from sesiones import ValorModuloFaltante


class Base(unittest.TestCase):
    def setUp(self):
        self.con = db.conectar(":memory:")
        db.guardar_modulo(self.con, Modulo(id=0, codigo="APE", nombre="APE"))
        db.guardar_modulo(self.con, Modulo(id=0, codigo="CAMI", nombre="CAMI"))
        self.os_id = db.guardar_obra_social(self.con, ObraSocial(
            id=0, nombre="SANCOR", cuit="30590354798",
            condicion_iva=CondicionIVA.EXENTO, dias_vencimiento=60))
        self.prof_id = db.guardar_profesional(
            self.con, Profesional(id=0, nombre="Lic. Ejemplo"))
        db.guardar_valor_modulo(self.con, ValorModulo("APE", 2026, 1, 16753.78))
        db.guardar_feriados(self.con, [Feriado(date(2026, 1, 1), "Año Nuevo")])

        self.pac_id = db.guardar_paciente(self.con, Paciente(
            id=0, apellido_nombre="Paciente de prueba", obra_social_id=self.os_id,
            modulo_codigo="APE", profesional_id=self.prof_id))
        db.guardar_autorizacion(self.con, Autorizacion(
            id=0, paciente_id=self.pac_id, vigencia_desde=date(2026, 1, 1),
            vigencia_hasta=date(2026, 12, 31),
            dias=[AutorizacionDia(d, "15:00") for d in (1, 2, 3, 4, 5)]))

    def tearDown(self):
        self.con.close()


class TestGuardadoYLectura(Base):
    def test_la_obra_social_vuelve_igual(self):
        o = db.obras_sociales(self.con)[self.os_id]
        self.assertEqual(o.nombre, "SANCOR")
        self.assertEqual(o.cuit, "30590354798")
        self.assertEqual(o.condicion_iva, CondicionIVA.EXENTO)
        self.assertFalse(o.tiene_tope)

    def test_la_base_rechaza_tope_activo_sin_valor(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute(
                "INSERT INTO obra_social (nombre, tiene_tope, tope_sesiones_mes) "
                "VALUES ('X', 1, NULL)")

    def test_la_autorizacion_conserva_los_horarios(self):
        a = db.autorizaciones(self.con, self.pac_id)[0]
        self.assertEqual(len(a.dias), 5)
        self.assertEqual(a.hora_de(3), "15:00")

    def test_guardar_la_autorizacion_de_nuevo_no_duplica_dias(self):
        a = db.autorizaciones(self.con, self.pac_id)[0]
        a.dias = [AutorizacionDia(1, "09:00"), AutorizacionDia(3, "09:00")]
        db.guardar_autorizacion(self.con, a)
        self.assertEqual(len(db.autorizaciones(self.con, self.pac_id)[0].dias), 2)

    def test_el_valor_del_modulo_se_pisa_sin_duplicar(self):
        db.guardar_valor_modulo(self.con, ValorModulo("APE", 2026, 1, 17000.0))
        valores = [v for v in db.valores_modulo(self.con, 2026)
                   if v.modulo_codigo == "APE" and v.mes == 1]
        self.assertEqual(len(valores), 1)
        self.assertEqual(valores[0].valor_sesion, 17000.0)

    def test_la_baja_de_paciente_es_logica(self):
        db.baja_paciente(self.con, self.pac_id)
        self.assertEqual(len(db.pacientes(self.con)), 0)
        self.assertEqual(len(db.pacientes(self.con, solo_activos=False)), 1)


class TestFacturacionDelMes(Base):
    def test_la_fila_trae_todo_precargado(self):
        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 2, 1))
        f = filas[0]
        self.assertEqual(f.sesiones, 21)               # 22 habiles - 1 feriado
        self.assertEqual(f.monto, 351829.38)           # el monto real de la planilla
        self.assertEqual(f.estado, EstadoCobranza.SIN_FACTURAR)
        self.assertIn("feriado", f.explicacion)

    def test_sin_valor_del_modulo_avisa_y_no_calcula_monto(self):
        filas = servicios.facturacion_del_mes(self.con, 2026, 3, date(2026, 4, 1))
        self.assertIsNone(filas[0].monto)
        self.assertIn("Falta el valor", filas[0].aviso)

    def test_sin_valor_del_modulo_no_deja_emitir(self):
        with self.assertRaises(ValorModuloFaltante):
            servicios.emitir_factura(self.con, self.pac_id, 2026, 3, "1",
                                     date(2026, 4, 10))

    def test_la_internacion_baja_las_sesiones(self):
        db.guardar_internacion(self.con, Internacion(
            paciente_id=self.pac_id, desde=date(2026, 1, 5), hasta=date(2026, 1, 16)))
        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 2, 1))
        self.assertEqual(filas[0].sesiones, 11)        # 21 - 10 habiles internado
        self.assertIn("internacion", filas[0].explicacion)

    def test_una_ausencia_cargada_a_mano_descuenta_una_sesion(self):
        db.guardar_excepcion(self.con, self.pac_id, date(2026, 1, 5),
                             MotivoNoFacturable.AUSENCIA, "no concurrio")
        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 2, 1))
        self.assertEqual(filas[0].sesiones, 20)
        self.assertIn("ausencia", filas[0].explicacion)

    def test_sin_autorizacion_vigente_lo_dice(self):
        otro = db.guardar_paciente(self.con, Paciente(
            id=0, apellido_nombre="Sin autorizacion", obra_social_id=self.os_id,
            modulo_codigo="APE"))
        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 2, 1))
        fila = next(f for f in filas if f.paciente.id == otro)
        self.assertEqual(fila.sesiones, 0)
        self.assertIn("Sin autorizacion", fila.aviso)


class TestCicloCompleto(Base):
    def emitir(self, nro="661"):
        return servicios.emitir_factura(self.con, self.pac_id, 2026, 1, nro,
                                        date(2026, 2, 10))

    def test_emitir_guarda_y_queda_pendiente(self):
        f = self.emitir()
        self.assertEqual(f.monto, 351829.38)
        guardadas = db.facturas(self.con, anio=2026, mes=1)
        self.assertEqual(len(guardadas), 1)
        self.assertEqual(guardadas[0].estado, EstadoFactura.EMITIDA)

        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 3, 1))
        self.assertEqual(filas[0].estado, EstadoCobranza.PENDIENTE)
        self.assertEqual(filas[0].color, "amarillo")

    def test_no_se_emite_dos_veces_el_mismo_mes(self):
        self.emitir()
        with self.assertRaises(OperacionNoPermitida) as e:
            self.emitir("662")
        self.assertIn("ya tiene la factura", str(e.exception))

    def test_a_los_60_dias_queda_en_rojo(self):
        self.emitir()
        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 4, 11))
        self.assertEqual(filas[0].estado, EstadoCobranza.ATRASADO)
        self.assertEqual(filas[0].color, "rojo")

    def test_pago_parcial_queda_azul_con_saldo(self):
        f = self.emitir()
        servicios.registrar_cobro(self.con, f.id, date(2026, 3, 6), monto=100000)
        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 3, 10))
        self.assertEqual(filas[0].estado, EstadoCobranza.PARCIAL)
        self.assertEqual(filas[0].color, "azul")
        self.assertEqual(filas[0].saldo, 251829.38)

    def test_pago_completo_queda_verde(self):
        f = self.emitir()
        servicios.registrar_cobro(self.con, f.id, date(2026, 3, 6))
        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 5, 1))
        self.assertEqual(filas[0].estado, EstadoCobranza.COBRADO)

    def test_anular_revierte_el_pago_y_libera_el_mes(self):
        f = self.emitir()
        servicios.registrar_cobro(self.con, f.id, date(2026, 3, 6))
        res, _ = servicios.anular_factura(self.con, f.id, "prueba", date(2026, 3, 20))
        self.assertEqual(len(res.reversiones), 1)

        pgs = db.pagos(self.con, f.id)
        self.assertEqual(len(pgs), 2)
        self.assertEqual(round(sum(p.monto for p in pgs), 2), 0.0)

        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 4, 1))
        self.assertEqual(filas[0].estado, EstadoCobranza.SIN_FACTURAR)
        # Y se puede volver a emitir, que es el punto de anular.
        self.assertEqual(self.emitir("662").monto, 351829.38)

    def test_revertir_un_pago_suelto_devuelve_el_color(self):
        f = self.emitir()
        p = servicios.registrar_cobro(self.con, f.id, date(2026, 3, 6))
        servicios.revertir_cobro(self.con, p.id, date(2026, 3, 20), "no se acredito")
        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 6, 1))
        self.assertEqual(filas[0].estado, EstadoCobranza.ATRASADO)

    def test_avisa_si_la_factura_ya_no_coincide_con_el_calendario(self):
        self.emitir()
        # Se carga un feriado despues de emitir: la factura no se toca, pero se avisa.
        db.guardar_feriados(self.con, [Feriado(date(2026, 1, 15), "feriado nuevo")])
        filas = servicios.facturacion_del_mes(self.con, 2026, 1, date(2026, 3, 1))
        self.assertIn("anularla y re-emitirla", filas[0].aviso)


class TestVistas(Base):
    def test_el_tablero_suma_por_paciente_y_por_mes(self):
        f = servicios.emitir_factura(self.con, self.pac_id, 2026, 1, "661",
                                     date(2026, 2, 10))
        t = servicios.tablero(self.con, 2026, date(2026, 3, 1))
        self.assertEqual(t.total_por_paciente[self.pac_id], f.monto)
        self.assertEqual(t.total_por_mes[1], f.monto)
        self.assertEqual(t.total_por_profesional[self.prof_id], f.monto)

    def test_el_resumen_separa_los_colores(self):
        f = servicios.emitir_factura(self.con, self.pac_id, 2026, 1, "661",
                                     date(2026, 2, 10))
        servicios.registrar_cobro(self.con, f.id, date(2026, 3, 6), monto=100000)
        r = servicios.resumen_anual(self.con, 2026, date(2026, 3, 10))
        self.assertEqual(r.facturado, f.monto)
        self.assertEqual(r.cobrado, 100000.0)
        self.assertEqual(r.parcial_pendiente, round(f.monto - 100000, 2))

    def test_la_ficha_cuenta_las_transcurridas_a_la_fecha(self):
        ficha = servicios.ficha_paciente(self.con, self.pac_id, 2026,
                                         date(2026, 1, 15))
        self.assertEqual(ficha["paciente"]["nombre"], "Paciente de prueba")
        self.assertEqual(ficha["meses"][0]["sesiones"], 21)
        self.assertEqual(ficha["meses"][0]["transcurridas"], 10)
        self.assertEqual(ficha["sesiones_transcurridas"], 10)

    def test_la_ficha_avisa_si_faltan_dni_o_cuil(self):
        ficha = servicios.ficha_paciente(self.con, self.pac_id, 2026,
                                         date(2026, 6, 1))
        self.assertFalse(ficha["paciente"]["datos_fiscales_completos"])

    def test_la_ficha_trae_las_fechas_generadas(self):
        ficha = servicios.ficha_paciente(self.con, self.pac_id, 2026,
                                         date(2026, 6, 1))
        enero = ficha["meses"][0]
        self.assertEqual(len(enero["fechas"]), 22)
        feriado = [f for f in enero["fechas"] if f["fecha"] == "2026-01-01"][0]
        self.assertFalse(feriado["facturable"])
        self.assertEqual(feriado["motivo"], "FERIADO")


class TestConfirmacionesSobreLaBase(Base):
    def test_cambiar_el_valor_cuenta_las_facturas_del_mes(self):
        servicios.emitir_factura(self.con, self.pac_id, 2026, 1, "661",
                                 date(2026, 2, 10))
        c = servicios.confirmacion_valor_modulo(self.con, "APE", 2026, 1, 18000.0)
        self.assertEqual(int(c.nivel), 2)
        self.assertIn("no se modifica", c.mensaje)

    def test_el_feriado_dice_a_cuantos_alcanza(self):
        c = servicios.confirmacion_feriado(self.con, date(2026, 5, 1), "prueba")
        self.assertIn("1 sesion prevista", c.mensaje)

    def test_anular_pide_escribir_el_numero(self):
        f = servicios.emitir_factura(self.con, self.pac_id, 2026, 1, "661",
                                     date(2026, 2, 10))
        c = servicios.confirmacion_anulacion(self.con, f.id)
        self.assertEqual(int(c.nivel), 3)
        self.assertEqual(c.texto_a_escribir, "661")


if __name__ == "__main__":
    unittest.main(verbosity=2)
