#!/usr/bin/env python3
"""Alta y mantenimiento de usuarios, desde la máquina del servidor.

    python3 usuarios.py crear --rol admin
    python3 usuarios.py listar
    python3 usuarios.py clave juana
    python3 usuarios.py rol juana operador
    python3 usuarios.py baja juana
    python3 usuarios.py alta juana
    python3 usuarios.py auditoria

La clave se pide por teclado y no se ve al escribirla; no se pasa por
argumento a propósito, porque los argumentos quedan en el historial del shell
y los ve cualquiera que liste los procesos de la máquina.

El primer usuario se crea con esta herramienta y no desde la web: así el
sistema nunca tiene una cuenta con clave conocida esperando a que alguien la
cambie.
"""

import argparse
import getpass
import sys

import autenticacion as auth
import persistencia as db


def pedir_clave(nombre_usuario: str) -> str:
    while True:
        clave = getpass.getpass("Clave (no se ve al escribir): ")
        try:
            auth.revisar_clave(clave, nombre_usuario)
        except auth.ErrorAutenticacion as e:
            print(f"  {e}\n")
            continue
        if clave != getpass.getpass("Repetir la clave: "):
            print("  Las claves no coinciden.\n")
            continue
        return clave


def cmd_crear(con, args):
    nombre_usuario = args.usuario or input("Nombre de usuario: ").strip()
    nombre = args.nombre or input("Nombre y apellido (opcional): ").strip()
    clave = pedir_clave(nombre_usuario)
    u = auth.crear_usuario(con, nombre_usuario, clave, args.rol, nombre)
    print(f"\nUsuario {u.nombre_usuario} creado con rol {u.rol}.")
    print(f"  {auth.ROLES[u.rol]}")


def cmd_listar(con, args):
    usuarios = auth.listar_usuarios(con)
    if not usuarios:
        print("No hay usuarios. Creá el primero con: "
              "python3 usuarios.py crear --rol admin")
        return
    print(f"{'usuario':24} {'rol':10} {'estado':8} último acceso")
    for u in usuarios:
        estado = "activo" if u.activo else "de baja"
        acceso = u.ultimo_acceso.strftime("%Y-%m-%d %H:%M") if u.ultimo_acceso else "nunca"
        marca = "  (debe cambiar la clave)" if u.debe_cambiar_clave else ""
        print(f"{u.nombre_usuario:24} {u.rol:10} {estado:8} {acceso}{marca}")


def cmd_clave(con, args):
    u = auth.buscar_por_nombre(con, args.usuario)
    if not u:
        sys.exit(f"No existe el usuario {args.usuario}.")
    clave = pedir_clave(u.nombre_usuario)
    auth.cambiar_clave(con, u.id, clave)
    print(f"Clave de {u.nombre_usuario} cambiada. Sus sesiones abiertas se cerraron.")


def cmd_rol(con, args):
    u = auth.buscar_por_nombre(con, args.usuario)
    if not u:
        sys.exit(f"No existe el usuario {args.usuario}.")
    auth.cambiar_rol(con, u.id, args.rol)
    print(f"{u.nombre_usuario} ahora es {args.rol}: {auth.ROLES[args.rol]}")


def cmd_baja(con, args):
    u = auth.buscar_por_nombre(con, args.usuario)
    if not u:
        sys.exit(f"No existe el usuario {args.usuario}.")
    otros = [x for x in auth.listar_usuarios(con)
             if x.rol == "admin" and x.activo and x.id != u.id]
    if u.rol == "admin" and not otros:
        sys.exit(f"{u.nombre_usuario} es el único administrador activo. "
                 f"Nombrá otro antes de darlo de baja.")
    auth.activar_usuario(con, u.id, False)
    print(f"{u.nombre_usuario} dado de baja. Sus sesiones se cerraron. "
          f"El historial queda completo.")


def cmd_alta(con, args):
    u = auth.buscar_por_nombre(con, args.usuario)
    if not u:
        sys.exit(f"No existe el usuario {args.usuario}.")
    auth.activar_usuario(con, u.id, True)
    print(f"{u.nombre_usuario} dado de alta.")


def cmd_auditoria(con, args):
    registros = auth.auditoria(con, args.limite)
    if not registros:
        print("No hay nada registrado todavía.")
        return
    print(f"{'momento':20} {'usuario':18} {'acción':26} detalle")
    for r in registros:
        print(f"{r['momento'][:19]:20} {r['usuario']:18} {r['accion']:26} "
              f"{r['detalle'][:60]}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="facturacion.db")
    sub = ap.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("crear", help="crear un usuario")
    c.add_argument("usuario", nargs="?")
    c.add_argument("--rol", choices=sorted(auth.ROLES), default="operador")
    c.add_argument("--nombre", default="")
    c.set_defaults(fn=cmd_crear)

    c = sub.add_parser("listar", help="ver los usuarios")
    c.set_defaults(fn=cmd_listar)

    c = sub.add_parser("clave", help="cambiar la clave de alguien")
    c.add_argument("usuario")
    c.set_defaults(fn=cmd_clave)

    c = sub.add_parser("rol", help="cambiar el rol")
    c.add_argument("usuario")
    c.add_argument("rol", choices=sorted(auth.ROLES))
    c.set_defaults(fn=cmd_rol)

    c = sub.add_parser("baja", help="dar de baja (no borra)")
    c.add_argument("usuario")
    c.set_defaults(fn=cmd_baja)

    c = sub.add_parser("alta", help="reactivar")
    c.add_argument("usuario")
    c.set_defaults(fn=cmd_alta)

    c = sub.add_parser("auditoria", help="ver quién hizo qué")
    c.add_argument("--limite", type=int, default=50)
    c.set_defaults(fn=cmd_auditoria)

    args = ap.parse_args()
    con = db.conectar(args.base)
    try:
        args.fn(con, args)
    except auth.ErrorAutenticacion as e:
        sys.exit(str(e))
    finally:
        con.close()


if __name__ == "__main__":
    main()
