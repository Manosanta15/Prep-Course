#!/usr/bin/env python3
"""Servidor HTTP del sistema. Solo biblioteca estandar: no hay que instalar nada.

    python3 datos_demo.py          # carga datos de prueba
    python3 servidor.py            # abre http://localhost:8080

La API devuelve JSON y es la misma que va a consumir el programa de gestion
administrativa cuando esto sea una parte de el. La pagina de web/ es un cliente
de esa API, no tiene logica propia: todas las reglas estan en el motor.

Para produccion hay que poner esto detras de un servidor real (gunicorn,
uvicorn, nginx) y agregar autenticacion. http.server alcanza para la puesta a
punto, no para publicar.
"""

import argparse
import json
import re
import traceback
from datetime import date
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import persistencia as db
import servicios
from facturacion import OperacionNoPermitida
from feriados import feriados_nacionales, solo_habiles
from modelo import (
    Autorizacion,
    AutorizacionDia,
    CondicionIVA,
    ConvenioApross,
    Feriado,
    Internacion,
    MedioPago,
    MotivoNoFacturable,
    ObraSocial,
    Paciente,
    ValorModulo,
)
from sesiones import ValorModuloFaltante

WEB = Path(__file__).parent.parent / "web"
TIPOS = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "application/javascript; charset=utf-8",
         ".svg": "image/svg+xml", ".ico": "image/x-icon"}


def hoy_de(params) -> date:
    """Permite mirar el tablero a una fecha distinta: ?hoy=2026-12-01.

    Es clave para la puesta a punto, porque el semaforo depende de la fecha y
    asi se puede ver como se va a ver en diciembre sin esperar.
    """
    v = params.get("hoy", [None])[0]
    return date.fromisoformat(v) if v else date.today()


def confirmacion_json(c) -> dict:
    return {"nivel": int(c.nivel), "titulo": c.titulo, "detalle": c.detalle,
            "mensaje": c.mensaje, "texto_a_escribir": c.texto_a_escribir}


class Handler(BaseHTTPRequestHandler):
    server_version = "FacturacionKine/0.1"

    def __init__(self, *args, base="facturacion.db", **kw):
        self.base = base
        super().__init__(*args, **kw)

    # ----- utilidades -------------------------------------------------
    def log_message(self, formato, *args):
        pass          # sin ruido en consola

    def responder(self, datos, codigo=200):
        cuerpo = json.dumps(datos, ensure_ascii=False, default=str).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def error(self, mensaje, codigo=400):
        self.responder({"error": mensaje}, codigo)

    def cuerpo(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}

    def archivo(self, ruta: str):
        if ruta in ("/", ""):
            ruta = "/index.html"
        destino = (WEB / ruta.lstrip("/")).resolve()
        if not str(destino).startswith(str(WEB.resolve())) or not destino.is_file():
            return self.error("No encontrado", 404)
        cuerpo = destino.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         TIPOS.get(destino.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    # ----- ruteo ------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self.archivo(u.path)
        con = db.conectar(self.base)
        try:
            self.get_api(u.path, parse_qs(u.query), con)
        except (OperacionNoPermitida, ValorModuloFaltante) as e:
            self.error(str(e))
        except Exception:
            traceback.print_exc()
            self.error("Error interno", 500)
        finally:
            con.close()

    def do_POST(self):
        u = urlparse(self.path)
        con = db.conectar(self.base)
        try:
            self.post_api(u.path, parse_qs(u.query), self.cuerpo(), con)
        except (OperacionNoPermitida, ValorModuloFaltante) as e:
            self.error(str(e))
        except (ValueError, KeyError) as e:
            self.error(f"Datos invalidos: {e}")
        except Exception:
            traceback.print_exc()
            self.error("Error interno", 500)
        finally:
            con.close()

    # ----- GET --------------------------------------------------------
    def get_api(self, ruta, params, con):
        anio = int(params.get("anio", [date.today().year])[0])
        hoy = hoy_de(params)

        if ruta == "/api/configuracion":
            return self.responder({
                "hoy": date.today().isoformat(),
                "obras_sociales": [
                    {"id": o.id, "nombre": o.nombre, "cuit": o.cuit,
                     "condicion_iva": o.condicion_iva.value,
                     "tiene_tope": o.tiene_tope,
                     "tope_sesiones_mes": o.tope_sesiones_mes,
                     "dias_vencimiento": o.dias_vencimiento, "activa": o.activa}
                    for o in db.obras_sociales(con).values()],
                "modulos": [{"codigo": m.codigo, "nombre": m.nombre,
                             "activo": m.activo} for m in db.modulos(con)],
                "profesionales": [{"id": p.id, "nombre": p.nombre,
                                   "activo": p.activo}
                                  for p in db.profesionales(con).values()],
                "valores": [{"modulo": v.modulo_codigo, "anio": v.anio,
                             "mes": v.mes, "valor_sesion": v.valor_sesion,
                             "tope_sesiones": v.tope_sesiones}
                            for v in db.valores_modulo(con, anio)],
                "feriados": [{"fecha": f.fecha.isoformat(),
                              "descripcion": f.descripcion, "tipo": f.tipo.value}
                             for f in db.feriados(con, anio)],
            })

        if ruta == "/api/pacientes":
            oses, profs = db.obras_sociales(con), db.profesionales(con)
            return self.responder([
                {"id": p.id, "nombre": p.apellido_nombre, "dni": p.dni,
                 "cuil": p.cuil, "nro_afiliado": p.nro_afiliado,
                 "obra_social": oses[p.obra_social_id].nombre,
                 "obra_social_id": p.obra_social_id, "modulo": p.modulo_codigo,
                 "profesional": profs[p.profesional_id].nombre
                 if p.profesional_id else None,
                 "profesional_id": p.profesional_id,
                 "datos_fiscales_completos": p.datos_fiscales_completos}
                for p in db.pacientes(con)])

        m = re.fullmatch(r"/api/facturacion/(\d{4})/(\d{1,2})", ruta)
        if m:
            a, mes = int(m[1]), int(m[2])
            filas = servicios.facturacion_del_mes(con, a, mes, hoy)
            return self.responder({
                "anio": a, "mes": mes, "hoy": hoy.isoformat(),
                "filas": [{
                    "paciente_id": f.paciente.id,
                    "paciente": f.paciente.apellido_nombre,
                    "obra_social": f.obra_social, "profesional": f.profesional,
                    "modulo": f.modulo, "sesiones": f.sesiones,
                    "explicacion": f.explicacion, "valor_sesion": f.valor_sesion,
                    "monto": f.monto, "tope_aplicado": f.tope_aplicado,
                    "factura_id": f.factura_id, "nro_factura": f.nro_factura,
                    "estado": f.estado.value, "color": f.color,
                    "cobrado": f.cobrado, "saldo": f.saldo, "dias": f.dias,
                    "aviso": f.aviso} for f in filas],
            })

        m = re.fullmatch(r"/api/tablero/(\d{4})", ruta)
        if m:
            a = int(m[1])
            t = servicios.tablero(con, a, hoy)
            r = servicios.resumen_anual(con, a, hoy)
            profs = db.profesionales(con)
            pacs = {p.id: p for p in db.pacientes(con, solo_activos=False)}
            return self.responder({
                "anio": a, "hoy": hoy.isoformat(),
                "pacientes": [
                    {"id": pid, "nombre": pacs[pid].apellido_nombre
                     if pid in pacs else f"#{pid}",
                     "total": t.total_por_paciente.get(pid, 0.0),
                     "meses": {str(mes): ({"monto": c.factura.monto,
                                           "color": c.color,
                                           "estado": c.estado.value,
                                           "saldo": c.saldo,
                                           "dias": c.dias,
                                           "nro": c.factura.nro_factura}
                                          if (c := t.celda(pid, mes)) else None)
                               for mes in range(1, 13)}}
                    for pid in sorted(t.celdas)],
                "total_por_mes": {str(k): v for k, v in t.total_por_mes.items()},
                "total_por_profesional": {
                    (profs[k].nombre if k in profs else "sin asignar"): v
                    for k, v in t.total_por_profesional.items()},
                "total_anual": t.total_anual,
                "resumen": {"facturado": r.facturado, "cobrado": r.cobrado,
                            "parcial_pendiente": r.parcial_pendiente,
                            "pendiente": r.pendiente, "atrasado": r.atrasado,
                            "por_cobrar": r.por_cobrar,
                            "cantidad_por_estado": {
                                e.value: n
                                for e, n in r.cantidad_por_estado.items()}},
            })

        m = re.fullmatch(r"/api/paciente/(\d+)/(\d{4})", ruta)
        if m:
            return self.responder(
                servicios.ficha_paciente(con, int(m[1]), int(m[2]), hoy))

        m = re.fullmatch(r"/api/factura/(\d+)/pagos", ruta)
        if m:
            return self.responder([
                {"id": p.id, "fecha_pago": p.fecha_pago.isoformat(),
                 "monto": p.monto, "medio_pago": p.medio_pago.value,
                 "observaciones": p.observaciones,
                 "es_reversion": p.es_reversion}
                for p in db.pagos(con, int(m[1]))])

        m = re.fullmatch(r"/api/apross/(\d{4})", ruta)
        if m:
            return self.responder([
                {"anio": c.anio, "mes": c.mes,
                 "cantidad_pacientes": c.cantidad_pacientes,
                 "cantidad_sesiones": c.cantidad_sesiones,
                 "valor_sesion": c.valor_sesion,
                 "porcentaje_descuento": c.porcentaje_descuento,
                 "total_bruto": c.total_bruto,
                 "monto_descuento": c.monto_descuento,
                 "importe_abonado": c.importe_abonado,
                 "fecha_pago": c.fecha_pago.isoformat() if c.fecha_pago else None,
                 "medio_pago": c.medio_pago.value if c.medio_pago else None,
                 "estado": estado}
                for c, estado in db.apross(con, int(m[1]))])

        if ruta == "/api/feriados-sugeridos":
            return self.responder([
                {"fecha": f.fecha.isoformat(), "descripcion": f.descripcion,
                 "tipo": f.tipo.value}
                for f in solo_habiles(feriados_nacionales(anio))])

        self.error("Ruta no encontrada", 404)

    # ----- POST -------------------------------------------------------
    def post_api(self, ruta, params, cuerpo, con):
        # --- confirmaciones: se piden antes de aplicar el cambio ---
        if ruta == "/api/confirmar/valor-modulo":
            return self.responder(confirmacion_json(
                servicios.confirmacion_valor_modulo(
                    con, cuerpo["modulo"], int(cuerpo["anio"]),
                    int(cuerpo["mes"]), float(cuerpo["valor_sesion"]))))

        if ruta == "/api/confirmar/feriado":
            return self.responder(confirmacion_json(
                servicios.confirmacion_feriado(
                    con, date.fromisoformat(cuerpo["fecha"]),
                    cuerpo.get("descripcion", ""))))

        if ruta == "/api/confirmar/anulacion":
            return self.responder(confirmacion_json(
                servicios.confirmacion_anulacion(con, int(cuerpo["factura_id"]))))

        if ruta == "/api/confirmar/baja-paciente":
            return self.responder(confirmacion_json(
                servicios.confirmacion_baja_paciente(
                    con, int(cuerpo["paciente_id"]))))

        # --- configuracion ---
        if ruta == "/api/valor-modulo":
            db.guardar_valor_modulo(con, ValorModulo(
                modulo_codigo=cuerpo["modulo"], anio=int(cuerpo["anio"]),
                mes=int(cuerpo["mes"]),
                valor_sesion=float(cuerpo["valor_sesion"]),
                tope_sesiones=(int(cuerpo["tope_sesiones"])
                               if cuerpo.get("tope_sesiones") else None)))
            return self.responder({"ok": True})

        if ruta == "/api/obra-social":
            db.guardar_obra_social(con, ObraSocial(
                id=int(cuerpo.get("id") or 0), nombre=cuerpo["nombre"],
                cuit=cuerpo.get("cuit"),
                condicion_iva=CondicionIVA(cuerpo.get("condicion_iva", "EXENTO")),
                tiene_tope=bool(cuerpo.get("tiene_tope")),
                tope_sesiones_mes=(int(cuerpo["tope_sesiones_mes"])
                                   if cuerpo.get("tope_sesiones_mes") else None),
                dias_vencimiento=int(cuerpo.get("dias_vencimiento", 60))))
            return self.responder({"ok": True})

        if ruta == "/api/feriado":
            db.guardar_feriados(con, [Feriado(
                fecha=date.fromisoformat(cuerpo["fecha"]),
                descripcion=cuerpo.get("descripcion", ""))])
            return self.responder({"ok": True})

        if ruta == "/api/feriado/borrar":
            db.borrar_feriado(con, date.fromisoformat(cuerpo["fecha"]))
            return self.responder({"ok": True})

        if ruta == "/api/feriados-sugeridos/importar":
            anio = int(cuerpo.get("anio", date.today().year))
            db.guardar_feriados(con, solo_habiles(feriados_nacionales(anio)))
            return self.responder({"ok": True,
                                   "cargados": len(db.feriados(con, anio))})

        # --- padron ---
        if ruta == "/api/paciente":
            pid = db.guardar_paciente(con, Paciente(
                id=int(cuerpo.get("id") or 0),
                apellido_nombre=cuerpo["nombre"],
                obra_social_id=int(cuerpo["obra_social_id"]),
                modulo_codigo=cuerpo["modulo"],
                profesional_id=(int(cuerpo["profesional_id"])
                                if cuerpo.get("profesional_id") else None),
                dni=cuerpo.get("dni") or None, cuil=cuerpo.get("cuil") or None,
                nro_afiliado=cuerpo.get("nro_afiliado") or None))
            return self.responder({"ok": True, "paciente_id": pid})

        if ruta == "/api/paciente/baja":
            db.baja_paciente(con, int(cuerpo["paciente_id"]))
            return self.responder({"ok": True})

        if ruta == "/api/autorizacion":
            aid = db.guardar_autorizacion(con, Autorizacion(
                id=int(cuerpo.get("id") or 0),
                paciente_id=int(cuerpo["paciente_id"]),
                vigencia_desde=date.fromisoformat(cuerpo["vigencia_desde"]),
                vigencia_hasta=(date.fromisoformat(cuerpo["vigencia_hasta"])
                                if cuerpo.get("vigencia_hasta") else None),
                dias=[AutorizacionDia(int(d["dia_semana"]), d.get("hora"))
                      for d in cuerpo.get("dias", [])],
                tope_sesiones_mes=(int(cuerpo["tope_sesiones_mes"])
                                   if cuerpo.get("tope_sesiones_mes") else None),
                observaciones=cuerpo.get("observaciones", "")))
            return self.responder({"ok": True, "autorizacion_id": aid})

        if ruta == "/api/internacion":
            db.guardar_internacion(con, Internacion(
                paciente_id=int(cuerpo["paciente_id"]),
                desde=date.fromisoformat(cuerpo["desde"]),
                hasta=(date.fromisoformat(cuerpo["hasta"])
                       if cuerpo.get("hasta") else None),
                institucion=cuerpo.get("institucion", ""),
                observaciones=cuerpo.get("observaciones", "")))
            return self.responder({"ok": True})

        if ruta == "/api/excepcion":
            db.guardar_excepcion(
                con, int(cuerpo["paciente_id"]),
                date.fromisoformat(cuerpo["fecha"]),
                MotivoNoFacturable(cuerpo.get("motivo", "AUSENCIA")),
                cuerpo.get("detalle", ""))
            return self.responder({"ok": True})

        # --- facturacion ---
        if ruta == "/api/factura/emitir":
            f = servicios.emitir_factura(
                con, int(cuerpo["paciente_id"]), int(cuerpo["anio"]),
                int(cuerpo["mes"]), str(cuerpo["nro_factura"]),
                date.fromisoformat(cuerpo["fecha_emision"]))
            return self.responder({"ok": True, "factura_id": f.id,
                                   "monto": f.monto,
                                   "cantidad_sesiones": f.cantidad_sesiones})

        if ruta == "/api/factura/anular":
            res, nueva = servicios.anular_factura(
                con, int(cuerpo["factura_id"]), cuerpo.get("motivo", ""),
                date.fromisoformat(cuerpo.get("fecha", date.today().isoformat())),
                bool(cuerpo.get("reemplazar")))
            return self.responder({"ok": True,
                                   "reversiones": len(res.reversiones),
                                   "nueva_factura_id": nueva.id if nueva else None})

        if ruta == "/api/pago":
            p = servicios.registrar_cobro(
                con, int(cuerpo["factura_id"]),
                date.fromisoformat(cuerpo["fecha_pago"]),
                float(cuerpo["monto"]) if cuerpo.get("monto") else None,
                cuerpo.get("medio_pago", "DEPOSITO"),
                cuerpo.get("observaciones", ""),
                bool(cuerpo.get("permitir_exceso")))
            return self.responder({"ok": True, "pago_id": p.id, "monto": p.monto})

        if ruta == "/api/pago/revertir":
            servicios.revertir_cobro(
                con, int(cuerpo["pago_id"]),
                date.fromisoformat(cuerpo.get("fecha", date.today().isoformat())),
                cuerpo.get("motivo", ""))
            return self.responder({"ok": True})

        # --- APROSS ---
        if ruta == "/api/apross":
            c = ConvenioApross(
                anio=int(cuerpo["anio"]), mes=int(cuerpo["mes"]),
                cantidad_pacientes=int(cuerpo.get("cantidad_pacientes", 0)),
                cantidad_sesiones=int(cuerpo.get("cantidad_sesiones", 0)),
                valor_sesion=float(cuerpo.get("valor_sesion", 0)),
                porcentaje_descuento=float(cuerpo.get("porcentaje_descuento", 4)),
                total_bruto_manual=(float(cuerpo["total_bruto_manual"])
                                    if cuerpo.get("total_bruto_manual") else None),
                fecha_pago=(date.fromisoformat(cuerpo["fecha_pago"])
                            if cuerpo.get("fecha_pago") else None),
                medio_pago=(MedioPago(cuerpo["medio_pago"])
                            if cuerpo.get("medio_pago") else None))
            db.guardar_apross(con, c, cuerpo.get("estado", "BORRADOR"))
            return self.responder({"ok": True, "total_bruto": c.total_bruto,
                                   "monto_descuento": c.monto_descuento,
                                   "importe_abonado": c.importe_abonado})

        self.error("Ruta no encontrada", 404)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="facturacion.db")
    ap.add_argument("--puerto", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 por defecto: no se expone a la red")
    args = ap.parse_args()

    db.conectar(args.base).close()      # crea el esquema si no existe
    servidor = ThreadingHTTPServer((args.host, args.puerto),
                                   partial(Handler, base=args.base))
    print(f"Sistema de facturacion en http://{args.host}:{args.puerto}")
    print(f"Base: {args.base}   (Ctrl+C para cortar)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")


if __name__ == "__main__":
    main()
