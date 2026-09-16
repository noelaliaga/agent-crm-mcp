#!/usr/bin/env python3
# PROTOTYPE, kept for provenance. Do not run against a real database.
#
# This is the MCP server as it was used with Hermes Agent in August 2026, copied from
# the original single file. The ONLY edits are marked "[sanitized]": the original read
# credentials from other projects' dotfiles (paths removed) and used the Supabase
# service_role key. Everything else, including its known weaknesses, is unchanged.
# The maintained version is servers/crm/server.py; docs/decisions.md explains what
# changed and why.
"""Servidor MCP sobre el CRM de prospeccion (Supabase).

Expone la tabla `prospectos` a Hermes por stdio. Sin dependencias: libreria
estandar. Las credenciales se leen del entorno [sanitized] y nunca salen por stdout.

ESCRITURA ACOTADA: las herramientas que escriben solo pueden tocar los campos
de la lista CAMPOS_ESCRIBIBLES, y solo sobre `prospectos` y `prospecto_notas`.
No hay ninguna ruta de codigo que borre nada.

Todo cambio en `prospectos` queda registrado por un trigger de la base de datos
en la tabla `prospecto_cambios`, venga de aqui o de cualquier otro sitio.
Ese registro se consulta con la herramienta crm_cambios.
"""

import json
import os  # [sanitized]
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

TABLA = "prospectos"
PROTO_POR_DEFECTO = "2025-06-18"


# --------------------------------------------------------------------- config

def _leer_env(path):
    valores = {}
    try:
        for linea in Path(path).read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            valores[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return valores


# [sanitized] The original merged two dotfiles from other projects (the agent runtime's
# and the CRM web app's). Here both values come from the environment instead.
_HERMES = dict(os.environ)  # [sanitized]
_CRM = {}  # [sanitized]

SB_URL = (_HERMES.get("SUPABASE_URL") or _CRM.get("NEXT_PUBLIC_SUPABASE_URL") or "").rstrip("/")
SB_KEY = _HERMES.get("SUPABASE_SERVICE_KEY") or ""  # service_role: see docs/decisions.md


def _get(**filtros):
    """Unica salida a la red. GET y nada mas."""
    if not SB_URL or not SB_KEY:
        raise RuntimeError(
            "Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en el entorno")  # [sanitized]
    qs = urllib.parse.urlencode(filtros, safe="().,*%:")
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/{TABLA}?{qs}",
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"},
        method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Supabase devolvio {e.code}. Revisa la clave")  # [sanitized]
    except urllib.error.URLError as e:
        raise RuntimeError(f"No llego a Supabase: {e.reason}")


def _patch(prospecto_id, cambios):
    """Actualiza campos de un prospecto. Solo los de CAMPOS_ESCRIBIBLES."""
    malos = set(cambios) - CAMPOS_ESCRIBIBLES
    if malos:
        raise RuntimeError("Estos campos no se pueden tocar desde aqui: " + ", ".join(sorted(malos)))
    datos = json.dumps(cambios).encode("utf-8")
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/{TABLA}?id=eq.{urllib.parse.quote(prospecto_id)}",
        data=datos, method="PATCH",
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
                 "Content-Type": "application/json", "Prefer": "return=representation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _post_nota(prospecto_id, cuerpo, autor="hermes"):
    datos = json.dumps({"prospecto_id": prospecto_id, "cuerpo": cuerpo,
                        "autor": autor}).encode("utf-8")
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/prospecto_notas", data=datos, method="POST",
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
                 "Content-Type": "application/json", "Prefer": "return=representation"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _cambios(**filtros):
    qs = urllib.parse.urlencode(filtros, safe="().,*%:")
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/prospecto_cambios?{qs}",
        headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


CAMPOS = ("id,nombre,ciudad,telefono,email,web,score,estado,tamano,hallazgos,propuesta,"
          "proximo_paso,proxima_fecha,fecha_ultimo_contacto,canal,origen")
CAMPOS_ESCRIBIBLES = {"estado", "proximo_paso", "proxima_fecha",
                      "fecha_ultimo_contacto", "resultado", "motivo_cierre"}
ESTADOS = ["nuevo", "contactado", "respondido", "agendado", "auditoria", "propuesta", "cerrado"]


# ---------------------------------------------------------------- formateadores

def _linea(p):
    canal = "email" if p.get("email") else ("tel " + p["telefono"] if p.get("telefono") else "sin canal")
    return (f"{p.get('nombre','?')} | {p.get('ciudad') or '-'} | {p.get('score')} pts | "
            f"{p.get('estado')} | {canal}")


def _ficha(p):
    out = [f"# {p.get('nombre')}",
           f"Ciudad: {p.get('ciudad') or '-'}",
           f"Puntuacion: {p.get('score')}",
           f"Estado: {p.get('estado')}",
           f"Tamano: {p.get('tamano') or '-'}",
           f"Telefono: {p.get('telefono') or '-'}",
           f"Email: {p.get('email') or '-'}",
           f"Web: {p.get('web') or '-'}"]
    h = p.get("hallazgos")
    if isinstance(h, list) and h:
        out.append("Hallazgos:")
        out += [f"  - {x}" for x in h]
    if p.get("propuesta"):
        out.append(f"Angulo propuesto: {p['propuesta']}")
    if p.get("fecha_ultimo_contacto"):
        out.append(f"Ultimo contacto: {p['fecha_ultimo_contacto']}")
    if p.get("proximo_paso"):
        out.append(f"Proximo paso: {p['proximo_paso']} ({p.get('proxima_fecha') or 'sin fecha'})")
    return "\n".join(out)


# ------------------------------------------------------------------- las tools

def t_buscar(args):
    nombre = (args.get("nombre") or "").strip()
    if not nombre:
        return "Dime un nombre o parte de un nombre."
    filas = _get(select=CAMPOS, nombre=f"ilike.*{nombre}*", limit="6")
    if not filas:
        return f"No hay ningun despacho que se parezca a «{nombre}»."
    if len(filas) > 1:
        return ("Hay varios, concreta:\n" + "\n".join("- " + f["nombre"] for f in filas))
    return _ficha(filas[0])


def t_hoy(args):
    hoy = date.today().isoformat()
    filas = _get(select="nombre,ciudad,estado,proximo_paso,proxima_fecha,telefono,canal,score",
                 proxima_fecha=f"lte.{hoy}", order="proxima_fecha.asc,score.desc", limit="40")
    if not filas:
        return "No vence nada. El CRM esta al dia."
    out = [f"{len(filas)} vencen hoy ({hoy}) o antes:"]
    for f in filas:
        atraso = (date.today() - date.fromisoformat(f["proxima_fecha"])).days
        marca = f" [+{atraso}d de retraso]" if atraso > 0 else ""
        tel = f" · {f['telefono']}" if f.get("canal") == "telefono" and f.get("telefono") else ""
        out.append(f"- {f['nombre']} ({f.get('ciudad') or '-'}, {f.get('score')} pts){tel}{marca}\n"
                   f"  {f.get('proximo_paso') or 'sin proximo paso'}")
    return "\n".join(out)


def t_pipeline(args):
    out = ["Embudo:"]
    for e in ESTADOS:
        filas = _get(select="nombre", estado=f"eq.{e}", limit="1000")
        if filas:
            out.append(f"  {e}: {len(filas)}")
    mejores = _get(select="nombre,ciudad,score,estado,telefono,email", estado="eq.nuevo",
                   order="score.desc", limit="8")
    out.append("\nMejores sin tocar:")
    out += ["  - " + _linea(p) for p in mejores]
    return "\n".join(out)


def t_consulta(args):
    f = {"select": CAMPOS, "order": "score.desc",
         "limit": str(min(int(args.get("limite") or 20), 60))}
    if args.get("ciudad"):
        f["ciudad"] = f"ilike.*{args['ciudad']}*"
    if args.get("estado"):
        f["estado"] = f"eq.{args['estado']}"
    if args.get("score_minimo") is not None:
        f["score"] = f"gte.{int(args['score_minimo'])}"
    canal = args.get("canal")
    if canal == "solo_telefono":
        f["email"] = "eq."
    elif canal == "con_email":
        f["email"] = "neq."
    filas = _get(**f)
    if not filas:
        return "Ningun prospecto cumple eso."
    return f"{len(filas)} resultados:\n" + "\n".join("- " + _linea(p) for p in filas)


def t_siguiente_llamada(args):
    filas = _get(select=CAMPOS, estado="eq.nuevo", email="eq.", order="score.desc", limit="5")
    if not filas:
        return "No queda nadie con telefono y sin email por tocar."
    return ("Siguientes por telefono, de mas a menos puntuacion:\n\n" +
            "\n\n".join(_ficha(p) for p in filas))



def _resolver(nombre):
    """Nombre -> (id, nombre real). Exige coincidencia unica: nunca adivina."""
    nombre = (nombre or "").strip()
    if not nombre:
        raise RuntimeError("Dime de que despacho se trata.")
    filas = _get(select="id,nombre", nombre=f"ilike.*{nombre}*", limit="6")
    if not filas:
        raise RuntimeError(f"No hay ningun despacho que se parezca a «{nombre}».")
    if len(filas) > 1:
        raise RuntimeError("Hay varios y no voy a elegir por ti: " +
                           ", ".join(f["nombre"] for f in filas))
    return filas[0]["id"], filas[0]["nombre"]


def t_actualizar_estado(args):
    estado = (args.get("estado") or "").strip().lower()
    if estado not in ESTADOS:
        return "Estado no valido. Los que hay: " + ", ".join(ESTADOS)
    pid, nom = _resolver(args.get("nombre"))
    antes = _get(select="estado", id=f"eq.{pid}")[0]["estado"]
    cambios = {"estado": estado}
    if args.get("motivo"):
        cambios["motivo_cierre"] = str(args["motivo"])[:300]
    _patch(pid, cambios)
    return (f"{nom}: estado {antes} -> {estado}. "
            "Queda registrado en prospecto_cambios.")


def t_programar(args):
    paso = (args.get("proximo_paso") or "").strip()
    if not paso:
        return "Dime cual es el proximo paso."
    fecha = (args.get("fecha") or "").strip()
    if fecha:
        try:
            date.fromisoformat(fecha)
        except ValueError:
            return "La fecha tiene que ir como AAAA-MM-DD."
    pid, nom = _resolver(args.get("nombre"))
    cambios = {"proximo_paso": paso[:400]}
    if fecha:
        cambios["proxima_fecha"] = fecha
    _patch(pid, cambios)
    return f"{nom}: proximo paso «{paso}»" + (f" para el {fecha}." if fecha else ".")


def t_apuntar_nota(args):
    cuerpo = (args.get("nota") or "").strip()
    if not cuerpo:
        return "La nota viene vacia."
    pid, nom = _resolver(args.get("nombre"))
    _post_nota(pid, cuerpo[:4000])
    return f"Nota apuntada en {nom} ({len(cuerpo)} caracteres)."


def t_cambios(args):
    limite = str(min(int(args.get("limite") or 25), 100))
    f = {"select": "nombre,operacion,campo,valor_antes,valor_despues,rol,creado_en",
         "order": "id.desc", "limit": limite}
    if args.get("desde"):
        f["creado_en"] = f"gte.{args['desde']}"
    if args.get("nombre"):
        f["nombre"] = f"ilike.*{args['nombre']}*"
    filas = _cambios(**f)
    if not filas:
        return "No hay cambios registrados con ese filtro."
    out = [f"{len(filas)} cambios, del mas reciente al mas antiguo:"]
    for c in filas:
        cuando = (c.get("creado_en") or "")[:19].replace("T", " ")
        if c["operacion"] == "UPDATE":
            out.append(f"- {cuando} · {c['nombre']} · {c['campo']}: "
                       f"«{c.get('valor_antes') or '-'}» -> «{c.get('valor_despues') or '-'}»")
        else:
            out.append(f"- {cuando} · {c['nombre']} · {c['operacion']}")
    return "\n".join(out)


TOOLS = [
    {"name": "crm_buscar",
     "description": "Ficha completa de un despacho del CRM de prospeccion por nombre: puntuacion, estado, canal, hallazgos de su web y proximo paso. Solo lectura.",
     "inputSchema": {"type": "object", "properties": {
         "nombre": {"type": "string", "description": "Nombre o parte del nombre del despacho"}},
         "required": ["nombre"]},
     "fn": t_buscar},
    {"name": "crm_hoy",
     "description": "Prospectos cuyo proximo paso vence hoy o esta retrasado, con el canal y el telefono si toca llamar. Solo lectura.",
     "inputSchema": {"type": "object", "properties": {}},
     "fn": t_hoy},
    {"name": "crm_pipeline",
     "description": "Estado del embudo: cuantos prospectos hay en cada estado y los mejores sin tocar todavia. Solo lectura.",
     "inputSchema": {"type": "object", "properties": {}},
     "fn": t_pipeline},
    {"name": "crm_consulta",
     "description": "Lista prospectos filtrando por ciudad, estado, puntuacion minima o canal disponible. Solo lectura.",
     "inputSchema": {"type": "object", "properties": {
         "ciudad": {"type": "string"},
         "estado": {"type": "string", "enum": ESTADOS},
         "score_minimo": {"type": "integer"},
         "canal": {"type": "string", "enum": ["solo_telefono", "con_email"]},
         "limite": {"type": "integer"}}},
     "fn": t_consulta},
    {"name": "crm_siguiente_llamada",
     "description": "Los siguientes despachos a los que llamar: sin email publicado, con telefono, ordenados por puntuacion, con sus hallazgos para abrir la llamada. Solo lectura.",
     "inputSchema": {"type": "object", "properties": {}},
     "fn": t_siguiente_llamada},
    {"name": "crm_actualizar_estado",
     "description": "Cambia el estado de un prospecto en el embudo. Escribe en el CRM y queda registrado.",
     "inputSchema": {"type": "object", "properties": {
         "nombre": {"type": "string"},
         "estado": {"type": "string", "enum": ESTADOS},
         "motivo": {"type": "string", "description": "Solo si se cierra"}},
         "required": ["nombre", "estado"]},
     "fn": t_actualizar_estado},
    {"name": "crm_programar_siguiente_paso",
     "description": "Fija el proximo paso de un prospecto y su fecha. Escribe en el CRM y queda registrado.",
     "inputSchema": {"type": "object", "properties": {
         "nombre": {"type": "string"},
         "proximo_paso": {"type": "string"},
         "fecha": {"type": "string", "description": "AAAA-MM-DD"}},
         "required": ["nombre", "proximo_paso"]},
     "fn": t_programar},
    {"name": "crm_apuntar_nota",
     "description": "Guarda una nota en la ficha de un prospecto. No modifica el estado ni las fechas.",
     "inputSchema": {"type": "object", "properties": {
         "nombre": {"type": "string"},
         "nota": {"type": "string"}},
         "required": ["nombre", "nota"]},
     "fn": t_apuntar_nota},
    {"name": "crm_cambios",
     "description": "Registro de auditoria: que se ha cambiado en el CRM, cuando, valor anterior y nuevo. Recoge los cambios vengan de donde vengan, tambien los hechos fuera de estas herramientas. Solo lectura.",
     "inputSchema": {"type": "object", "properties": {
         "nombre": {"type": "string", "description": "Filtrar por despacho"},
         "desde": {"type": "string", "description": "Fecha AAAA-MM-DD"},
         "limite": {"type": "integer"}}},
     "fn": t_cambios},
]
POR_NOMBRE = {t["name"]: t for t in TOOLS}


# ------------------------------------------------------------------ jsonrpc

def _responder(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _manejar(msg):
    metodo = msg.get("method")
    mid = msg.get("id")

    if metodo == "initialize":
        proto = (msg.get("params") or {}).get("protocolVersion") or PROTO_POR_DEFECTO
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "crm-prospeccion", "version": "1.0.0"}}}

    if metodo in ("notifications/initialized", "initialized"):
        return None

    if metodo == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if metodo == "tools/list":
        listado = [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": listado}}

    if metodo == "tools/call":
        params = msg.get("params") or {}
        nombre = params.get("name")
        args = params.get("arguments") or {}
        tool = POR_NOMBRE.get(nombre)
        if not tool:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"Herramienta desconocida: {nombre}"}}
        try:
            texto = tool["fn"](args)
            err = False
        except Exception as e:
            texto = f"Error consultando el CRM: {e}"
            err = True
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": texto}], "isError": err}}

    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"Metodo no soportado: {metodo}"}}


def main():
    for linea in sys.stdin:
        linea = linea.strip()
        if not linea:
            continue
        try:
            msg = json.loads(linea)
        except json.JSONDecodeError:
            continue
        try:
            r = _manejar(msg)
        except Exception as e:
            r = {"jsonrpc": "2.0", "id": msg.get("id"),
                 "error": {"code": -32603, "message": str(e)}}
        if r is not None:
            _responder(r)


if __name__ == "__main__":
    main()
