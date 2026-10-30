"""Tests de usuarios, claves y sesiones.

Varios de estos no prueban que algo ande, sino que algo **no** ande: que la
clave no se guarde, que un usuario de lectura no pueda facturar, que probar
claves de a una termine en bloqueo.
"""

import unittest
from datetime import timedelta

import autenticacion as auth
import persistencia as db

CLAVE = "una frase larga de prueba"


class Base(unittest.TestCase):
    def setUp(self):
        self.con = db.conectar(":memory:")
        self.admin = auth.crear_usuario(self.con, "admin.prueba", CLAVE, "admin",
                                        "Admin de prueba")

    def tearDown(self):
        self.con.close()


class TestClaves(Base):
    def test_la_clave_no_queda_guardada(self):
        fila = self.con.execute("SELECT clave_hash FROM usuario").fetchone()
        self.assertNotIn(CLAVE, fila["clave_hash"])
        self.assertTrue(fila["clave_hash"].startswith("scrypt$"))

    def test_dos_usuarios_con_la_misma_clave_dan_hashes_distintos(self):
        auth.crear_usuario(self.con, "otro.usuario", CLAVE, "operador")
        hashes = [f["clave_hash"] for f in
                  self.con.execute("SELECT clave_hash FROM usuario")]
        self.assertEqual(len(set(hashes)), 2)   # la sal es por usuario

    def test_verifica_la_correcta_y_rechaza_las_demas(self):
        h = auth.hashear_clave(CLAVE)
        self.assertTrue(auth.verificar_clave(CLAVE, h))
        self.assertFalse(auth.verificar_clave(CLAVE + "x", h))
        self.assertFalse(auth.verificar_clave("", h))

    def test_un_hash_corrupto_no_rompe_sino_que_rechaza(self):
        self.assertFalse(auth.verificar_clave(CLAVE, "cualquier cosa"))
        self.assertFalse(auth.verificar_clave(CLAVE, "scrypt$x$y$z$q$w"))

    def test_rechaza_claves_cortas(self):
        with self.assertRaises(auth.ErrorAutenticacion):
            auth.crear_usuario(self.con, "corto", "abc123", "operador")

    def test_rechaza_la_clave_igual_al_usuario(self):
        with self.assertRaises(auth.ErrorAutenticacion):
            auth.crear_usuario(self.con, "repetida.clave", "repetida.clave")

    def test_detecta_un_hash_con_parametros_viejos(self):
        viejo = f"scrypt${2 ** 12}$8$1$" + "00" * 16 + "$" + "00" * 32
        self.assertTrue(auth.hay_que_rehashear(viejo))
        self.assertFalse(auth.hay_que_rehashear(auth.hashear_clave(CLAVE)))

    def test_al_entrar_se_rehashea_una_clave_vieja(self):
        viejo = auth.hashear_clave(CLAVE).split("$")
        viejo[1] = str(2 ** 12)      # simula parametros de antes
        import hashlib
        sal = bytes.fromhex(viejo[4])
        viejo[5] = hashlib.scrypt(CLAVE.encode(), salt=sal, n=2 ** 12, r=8, p=1,
                                  dklen=32, maxmem=auth.SCRYPT_MAXMEM).hex()
        self.con.execute("UPDATE usuario SET clave_hash = ? WHERE id = ?",
                         ("$".join(viejo), self.admin.id))
        self.con.commit()

        auth.ingresar(self.con, "admin.prueba", CLAVE)
        nuevo = self.con.execute("SELECT clave_hash FROM usuario WHERE id = ?",
                                 (self.admin.id,)).fetchone()["clave_hash"]
        self.assertFalse(auth.hay_que_rehashear(nuevo))


class TestIngreso(Base):
    def test_ingreso_correcto_abre_sesion(self):
        s = auth.ingresar(self.con, "admin.prueba", CLAVE)
        self.assertEqual(s.usuario.nombre_usuario, "admin.prueba")
        self.assertTrue(s.token)
        self.assertTrue(s.csrf)
        self.assertNotEqual(s.token, s.csrf)

    def test_el_token_se_guarda_hasheado(self):
        s = auth.ingresar(self.con, "admin.prueba", CLAVE)
        guardado = self.con.execute("SELECT token_hash FROM sesion").fetchone()
        self.assertNotEqual(guardado["token_hash"], s.token)
        self.assertNotIn(s.token, guardado["token_hash"])

    def test_el_mensaje_no_distingue_usuario_de_clave(self):
        with self.assertRaises(auth.ErrorAutenticacion) as sin_usuario:
            auth.ingresar(self.con, "no.existe", CLAVE)
        with self.assertRaises(auth.ErrorAutenticacion) as mala_clave:
            auth.ingresar(self.con, "admin.prueba", "otra clave larga aca")
        self.assertEqual(str(sin_usuario.exception), str(mala_clave.exception))

    def test_el_nombre_de_usuario_no_distingue_mayusculas(self):
        s = auth.ingresar(self.con, "ADMIN.PRUEBA", CLAVE)
        self.assertEqual(s.usuario.id, self.admin.id)

    def test_bloquea_despues_de_varios_intentos(self):
        for _ in range(auth.INTENTOS_ANTES_DE_BLOQUEAR - 1):
            with self.assertRaises(auth.ErrorAutenticacion):
                auth.ingresar(self.con, "admin.prueba", "clave equivocada aca")
        with self.assertRaises(auth.ErrorAutenticacion) as e:
            auth.ingresar(self.con, "admin.prueba", "clave equivocada aca")
        self.assertIn("bloqueado", str(e.exception).lower())

        # Y ahora ni la clave correcta entra, hasta que pase el bloqueo.
        with self.assertRaises(auth.ErrorAutenticacion) as e2:
            auth.ingresar(self.con, "admin.prueba", CLAVE)
        self.assertIn("bloqueado", str(e2.exception).lower())

    def test_un_ingreso_correcto_limpia_los_intentos(self):
        with self.assertRaises(auth.ErrorAutenticacion):
            auth.ingresar(self.con, "admin.prueba", "clave equivocada aca")
        auth.ingresar(self.con, "admin.prueba", CLAVE)
        fila = self.con.execute("SELECT intentos_fallidos FROM usuario "
                                "WHERE id = ?", (self.admin.id,)).fetchone()
        self.assertEqual(fila["intentos_fallidos"], 0)

    def test_una_cuenta_dada_de_baja_no_entra(self):
        auth.activar_usuario(self.con, self.admin.id, False)
        with self.assertRaises(auth.ErrorAutenticacion) as e:
            auth.ingresar(self.con, "admin.prueba", CLAVE)
        self.assertIn("baja", str(e.exception))


class TestSesiones(Base):
    def test_el_token_identifica_al_usuario(self):
        s = auth.ingresar(self.con, "admin.prueba", CLAVE)
        usuario, csrf = auth.sesion_de_token(self.con, s.token)
        self.assertEqual(usuario.id, self.admin.id)
        self.assertEqual(csrf, s.csrf)

    def test_un_token_inventado_no_sirve(self):
        auth.ingresar(self.con, "admin.prueba", CLAVE)
        self.assertIsNone(auth.sesion_de_token(self.con, "token-inventado"))
        self.assertIsNone(auth.sesion_de_token(self.con, None))
        self.assertIsNone(auth.sesion_de_token(self.con, ""))

    def test_cerrar_sesion_invalida_el_token(self):
        s = auth.ingresar(self.con, "admin.prueba", CLAVE)
        auth.cerrar_sesion(self.con, s.token)
        self.assertIsNone(auth.sesion_de_token(self.con, s.token))

    def test_una_sesion_vencida_no_sirve_y_se_borra(self):
        s = auth.ingresar(self.con, "admin.prueba", CLAVE)
        vencida = auth._iso(auth.ahora() - timedelta(minutes=1))
        self.con.execute("UPDATE sesion SET expira = ?", (vencida,))
        self.con.commit()
        self.assertIsNone(auth.sesion_de_token(self.con, s.token))
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) c FROM sesion").fetchone()["c"], 0)

    def test_dar_de_baja_corta_las_sesiones_abiertas(self):
        s = auth.ingresar(self.con, "admin.prueba", CLAVE)
        auth.activar_usuario(self.con, self.admin.id, False)
        self.assertIsNone(auth.sesion_de_token(self.con, s.token))

    def test_cambiar_la_clave_cierra_las_otras_sesiones(self):
        s = auth.ingresar(self.con, "admin.prueba", CLAVE)
        auth.cambiar_clave(self.con, self.admin.id, "otra frase larga distinta",
                           clave_actual=CLAVE)
        self.assertIsNone(auth.sesion_de_token(self.con, s.token))

    def test_no_se_cambia_la_clave_sin_saber_la_actual(self):
        with self.assertRaises(auth.ErrorAutenticacion):
            auth.cambiar_clave(self.con, self.admin.id, "otra frase larga aca",
                               clave_actual="la que no es")


class TestRoles(Base):
    def test_admin_puede_todo(self):
        u = auth.buscar_usuario(self.con, self.admin.id)
        for p in ("ver", "facturar", "configurar", "usuarios"):
            self.assertTrue(u.puede(p), p)

    def test_operador_factura_pero_no_configura(self):
        u = auth.crear_usuario(self.con, "operador.prueba", CLAVE, "operador")
        self.assertTrue(u.puede("ver"))
        self.assertTrue(u.puede("facturar"))
        self.assertFalse(u.puede("configurar"))
        self.assertFalse(u.puede("usuarios"))

    def test_lectura_solo_mira(self):
        u = auth.crear_usuario(self.con, "lectura.prueba", CLAVE, "lectura")
        self.assertTrue(u.puede("ver"))
        self.assertFalse(u.puede("facturar"))

    def test_un_usuario_inactivo_no_puede_nada(self):
        u = auth.crear_usuario(self.con, "baja.prueba", CLAVE, "admin")
        auth.activar_usuario(self.con, u.id, False)
        self.assertFalse(auth.buscar_usuario(self.con, u.id).puede("ver"))

    def test_no_acepta_un_rol_inventado(self):
        with self.assertRaises(auth.ErrorAutenticacion):
            auth.crear_usuario(self.con, "raro.prueba", CLAVE, "superusuario")


class TestAuditoria(Base):
    def test_registra_quien_y_que(self):
        auth.auditar(self.con, self.admin, "factura.emitir",
                     "factura 661 por 351.829,38", "10.0.0.5")
        registro = auth.auditoria(self.con)[0]
        self.assertEqual(registro["usuario"], "admin.prueba")
        self.assertEqual(registro["accion"], "factura.emitir")
        self.assertIn("661", registro["detalle"])

    def test_el_mas_reciente_queda_primero(self):
        auth.auditar(self.con, self.admin, "primera")
        auth.auditar(self.con, self.admin, "segunda")
        self.assertEqual(auth.auditoria(self.con)[0]["accion"], "segunda")


if __name__ == "__main__":
    unittest.main(verbosity=2)
