"""Tests de la capa HTTP: sesión, CSRF y permisos por rol.

Estos no prueban reglas de negocio, prueban que no se pueda entrar por el
costado. Levantan el servidor de verdad en un puerto libre y le pegan como
le pegaría un navegador, o como le pegaría otro sitio.
"""

import http.client
import json
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import autenticacion as auth
import persistencia as db
import servidor
from modelo import Modulo

CLAVE_ADMIN = "clave larga de la admin"
CLAVE_OPER = "clave larga del operador"
CLAVE_LEE = "clave larga de lectura"


class ServidorDePrueba(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.carpeta = tempfile.TemporaryDirectory()
        cls.ruta = str(Path(cls.carpeta.name) / "prueba.db")

        con = db.conectar(cls.ruta)
        auth.crear_usuario(con, "la.admin", CLAVE_ADMIN, "admin")
        auth.crear_usuario(con, "el.oper", CLAVE_OPER, "operador")
        auth.crear_usuario(con, "la.lee", CLAVE_LEE, "lectura")
        db.guardar_modulo(con, Modulo(id=0, codigo="APE", nombre="APE"))
        con.close()

        cls.servidor = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(servidor.Handler, base=cls.ruta))
        cls.puerto = cls.servidor.server_address[1]
        cls.hilo = threading.Thread(target=cls.servidor.serve_forever, daemon=True)
        cls.hilo.start()

    @classmethod
    def tearDownClass(cls):
        cls.servidor.shutdown()
        cls.servidor.server_close()
        cls.carpeta.cleanup()

    def setUp(self):
        # El freno por IP es del proceso: se limpia entre tests para que el
        # orden en que corren no cambie el resultado.
        servidor.LIMITE.limpiar("127.0.0.1")

    # ----- helpers ----------------------------------------------------
    def pedir(self, metodo, ruta, cuerpo=None, cookie=None, csrf=None):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        cabeceras = {}
        datos = None
        if cuerpo is not None:
            datos = json.dumps(cuerpo)
            cabeceras["Content-Type"] = "application/json"
        if cookie:
            cabeceras["Cookie"] = f"{servidor.COOKIE}={cookie}"
        if csrf:
            cabeceras["X-CSRF-Token"] = csrf
        c.request(metodo, ruta, datos, cabeceras)
        r = c.getresponse()
        crudo = r.read().decode()
        galleta = r.getheader("Set-Cookie")
        c.close()
        try:
            return r.status, json.loads(crudo), galleta
        except json.JSONDecodeError:
            return r.status, {}, galleta

    def entrar(self, usuario, clave):
        estado, cuerpo, galleta = self.pedir(
            "POST", "/api/sesion", {"usuario": usuario, "clave": clave})
        self.assertEqual(estado, 200, cuerpo)
        token = galleta.split(";")[0].split("=", 1)[1]
        return token, cuerpo["csrf"]


class TestSinSesion(ServidorDePrueba):
    def test_la_api_no_responde_sin_sesion(self):
        for ruta in ("/api/tablero/2026", "/api/pacientes", "/api/configuracion",
                     "/api/usuarios", "/api/auditoria"):
            estado, _, _ = self.pedir("GET", ruta)
            self.assertEqual(estado, 401, ruta)

    def test_tampoco_se_puede_escribir_sin_sesion(self):
        estado, _, _ = self.pedir("POST", "/api/factura/emitir", {
            "paciente_id": 1, "anio": 2026, "mes": 1, "nro_factura": "1",
            "fecha_emision": "2026-02-10"})
        self.assertEqual(estado, 401)

    def test_la_pagina_si_se_sirve(self):
        # No trae datos: los pide a la API, que sí exige sesión.
        estado, _, _ = self.pedir("GET", "/")
        self.assertEqual(estado, 200)


class TestIngreso(ServidorDePrueba):
    def test_entrar_devuelve_cookie_bien_marcada(self):
        estado, cuerpo, galleta = self.pedir(
            "POST", "/api/sesion", {"usuario": "la.admin", "clave": CLAVE_ADMIN})
        self.assertEqual(estado, 200)
        self.assertIn("HttpOnly", galleta)       # no la lee el javascript
        self.assertIn("SameSite=Strict", galleta)  # no viaja desde otro sitio
        self.assertIn("csrf", cuerpo)

    def test_el_token_de_sesion_no_viaja_en_el_cuerpo(self):
        _, cuerpo, galleta = self.pedir(
            "POST", "/api/sesion", {"usuario": "la.admin", "clave": CLAVE_ADMIN})
        token = galleta.split(";")[0].split("=", 1)[1]
        self.assertNotIn(token, json.dumps(cuerpo))

    def test_clave_incorrecta_no_entra(self):
        estado, _, _ = self.pedir(
            "POST", "/api/sesion", {"usuario": "la.admin", "clave": "no es"})
        self.assertEqual(estado, 401)

    def test_el_mensaje_no_dice_si_el_usuario_existe(self):
        _, a, _ = self.pedir("POST", "/api/sesion",
                             {"usuario": "la.admin", "clave": "clave que no es"})
        _, b, _ = self.pedir("POST", "/api/sesion",
                             {"usuario": "no.existe", "clave": "clave que no es"})
        self.assertEqual(a["error"], b["error"])

    def test_cerrar_sesion_invalida_la_cookie(self):
        token, csrf = self.entrar("la.admin", CLAVE_ADMIN)
        self.pedir("POST", "/api/sesion/cerrar", {}, token, csrf)
        estado, _, _ = self.pedir("GET", "/api/tablero/2026", cookie=token)
        self.assertEqual(estado, 401)

    def test_una_cookie_inventada_no_sirve(self):
        estado, _, _ = self.pedir("GET", "/api/tablero/2026",
                                  cookie="token-inventado-cualquiera")
        self.assertEqual(estado, 401)

    def test_frena_los_intentos_repetidos_desde_una_ip(self):
        vistos = []
        for i in range(servidor.LIMITE.maximo + 2):
            estado, _, _ = self.pedir("POST", "/api/sesion",
                                      {"usuario": f"inventado{i}",
                                       "clave": "probando claves"})
            vistos.append(estado)
        self.assertIn(429, vistos)
        self.assertEqual(vistos[-1], 429)


class TestCSRF(ServidorDePrueba):
    def test_un_post_con_cookie_pero_sin_csrf_se_rechaza(self):
        """Es lo que podria hacer otro sitio: tiene la cookie del navegador,
        pero no puede leer el token de la sesion."""
        token, _ = self.entrar("la.admin", CLAVE_ADMIN)
        estado, _, _ = self.pedir("POST", "/api/valor-modulo", {
            "modulo": "APE", "anio": 2026, "mes": 1, "valor_sesion": 1000},
            cookie=token)
        self.assertEqual(estado, 403)

    def test_un_csrf_de_otra_sesion_tampoco_sirve(self):
        token, _ = self.entrar("la.admin", CLAVE_ADMIN)
        _, otro_csrf = self.entrar("el.oper", CLAVE_OPER)
        estado, _, _ = self.pedir("POST", "/api/valor-modulo", {
            "modulo": "APE", "anio": 2026, "mes": 1, "valor_sesion": 1000},
            cookie=token, csrf=otro_csrf)
        self.assertEqual(estado, 403)

    def test_con_el_csrf_propio_funciona(self):
        token, csrf = self.entrar("la.admin", CLAVE_ADMIN)
        estado, cuerpo, _ = self.pedir("POST", "/api/valor-modulo", {
            "modulo": "APE", "anio": 2026, "mes": 1, "valor_sesion": 16753.78},
            cookie=token, csrf=csrf)
        self.assertEqual(estado, 200, cuerpo)


class TestRoles(ServidorDePrueba):
    def test_lectura_mira_pero_no_escribe(self):
        token, csrf = self.entrar("la.lee", CLAVE_LEE)
        self.assertEqual(self.pedir("GET", "/api/tablero/2026", cookie=token)[0], 200)
        estado, _, _ = self.pedir("POST", "/api/factura/emitir", {
            "paciente_id": 1, "anio": 2026, "mes": 1, "nro_factura": "1",
            "fecha_emision": "2026-02-10"}, token, csrf)
        self.assertEqual(estado, 403)

    def test_operador_factura_pero_no_configura(self):
        token, csrf = self.entrar("el.oper", CLAVE_OPER)
        estado, _, _ = self.pedir("POST", "/api/valor-modulo", {
            "modulo": "APE", "anio": 2026, "mes": 2, "valor_sesion": 1000},
            token, csrf)
        self.assertEqual(estado, 403)

    def test_solo_un_admin_ve_y_toca_usuarios(self):
        for usuario, clave in (("el.oper", CLAVE_OPER), ("la.lee", CLAVE_LEE)):
            token, csrf = self.entrar(usuario, clave)
            self.assertEqual(self.pedir("GET", "/api/usuarios", cookie=token)[0],
                             403, usuario)
            self.assertEqual(self.pedir("GET", "/api/auditoria", cookie=token)[0],
                             403, usuario)
            estado, _, _ = self.pedir("POST", "/api/usuario", {
                "usuario": "colado", "clave": "clave larga del colado",
                "rol": "admin"}, token, csrf)
            self.assertEqual(estado, 403, usuario)

    def test_una_ruta_que_no_figura_en_la_tabla_queda_cerrada(self):
        """Por omisión pide el permiso más alto, así una ruta nueva no queda
        abierta por olvido."""
        self.assertEqual(servidor.PERMISOS.get("/api/ruta/nueva", "configurar"),
                         "configurar")
        token, csrf = self.entrar("el.oper", CLAVE_OPER)
        estado, _, _ = self.pedir("POST", "/api/ruta/nueva", {}, token, csrf)
        self.assertEqual(estado, 403)

    def test_un_admin_no_puede_cambiarse_el_rol_a_si_mismo(self):
        token, csrf = self.entrar("la.admin", CLAVE_ADMIN)
        con = db.conectar(self.ruta)
        yo = auth.buscar_por_nombre(con, "la.admin").id
        con.close()
        estado, cuerpo, _ = self.pedir("POST", "/api/usuario/rol",
                                       {"usuario_id": yo, "rol": "lectura"},
                                       token, csrf)
        self.assertEqual(estado, 400)
        self.assertIn("vos mismo", cuerpo["error"])

    def test_no_se_puede_dar_de_baja_al_ultimo_admin(self):
        token, csrf = self.entrar("la.admin", CLAVE_ADMIN)
        con = db.conectar(self.ruta)
        otra = auth.crear_usuario(con, "otra.admin", "clave larga de la otra",
                                  "admin")
        con.close()
        # Con dos admins se puede dar de baja a uno...
        estado, _, _ = self.pedir("POST", "/api/usuario/activar",
                                  {"usuario_id": otra.id, "activo": False},
                                  token, csrf)
        self.assertEqual(estado, 200)
        # ...pero no al que queda.
        con = db.conectar(self.ruta)
        yo = auth.buscar_por_nombre(con, "la.admin").id
        con.close()
        estado, cuerpo, _ = self.pedir("POST", "/api/usuario/activar",
                                       {"usuario_id": yo, "activo": False},
                                       token, csrf)
        self.assertEqual(estado, 400)


class TestCabeceras(ServidorDePrueba):
    def test_manda_las_cabeceras_de_seguridad(self):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        c.request("GET", "/api/sesion/actual")
        r = c.getresponse()
        r.read()
        self.assertIn("frame-ancestors 'none'",
                      r.getheader("Content-Security-Policy") or "")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        c.close()

    def test_no_dice_que_version_corre(self):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        c.request("GET", "/api/sesion/actual")
        r = c.getresponse()
        r.read()
        self.assertNotIn("Python", r.getheader("Server") or "")
        c.close()

    def test_no_sirve_archivos_de_afuera_de_web(self):
        for ruta in ("/../motor/persistencia.py", "/../../README.md",
                     "/..%2fmotor%2fautenticacion.py"):
            estado, _, _ = self.pedir("GET", ruta)
            self.assertIn(estado, (400, 404), ruta)


class TestAuditoriaHTTP(ServidorDePrueba):
    def test_queda_registrado_quien_emitio(self):
        token, csrf = self.entrar("la.admin", CLAVE_ADMIN)
        self.pedir("POST", "/api/valor-modulo", {
            "modulo": "APE", "anio": 2026, "mes": 5, "valor_sesion": 19346.37},
            token, csrf)
        estado, registros, _ = self.pedir("GET", "/api/auditoria", cookie=token)
        self.assertEqual(estado, 200)
        acciones = [(r["usuario"], r["accion"]) for r in registros]
        self.assertIn(("la.admin", "config.valor-modulo"), acciones)

    def test_los_intentos_fallidos_quedan_registrados(self):
        self.pedir("POST", "/api/sesion",
                   {"usuario": "la.admin", "clave": "clave que no es"})
        token, _ = self.entrar("la.admin", CLAVE_ADMIN)
        _, registros, _ = self.pedir("GET", "/api/auditoria", cookie=token)
        self.assertIn("sesion.fallida", [r["accion"] for r in registros])

    def test_la_auditoria_no_guarda_claves(self):
        token, csrf = self.entrar("la.admin", CLAVE_ADMIN)
        self.pedir("POST", "/api/usuario", {
            "usuario": "nueva.persona", "clave": "una clave muy secreta",
            "rol": "lectura"}, token, csrf)
        _, registros, _ = self.pedir("GET", "/api/auditoria", cookie=token)
        todo = json.dumps(registros)
        self.assertNotIn("una clave muy secreta", todo)
        self.assertIn("usuario.crear", [r["accion"] for r in registros])


if __name__ == "__main__":
    unittest.main(verbosity=2)
