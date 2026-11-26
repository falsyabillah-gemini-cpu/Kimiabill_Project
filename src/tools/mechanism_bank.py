"""Dynamic Mechanistic Animation Bank for Kimiabill Studio.

Stores the 12 core procedural 30fps visualization engines plus an expanding library
of reusable topic-specific presets (`data/mechanism_bank.json`). Agent 8 automatically
audits weekly mechanism-to-topic accuracy and appends new presets into the bank so
Agent 3 and Agent 4 produce diverse, topic-matched animations without repetition.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BANK_PATH = BASE_DIR / "data" / "mechanism_bank.json"

BUILTIN_MECHANISM_ENGINES: dict[str, dict[str, Any]] = {
    "electron_transfer": {
        "name": "Transfer Elektron (Redoks / Korosi / Baterai)",
        "when_to_use": "Oksidasi-reduksi, perkaratan logam, ion listrik, baterai, elektrokimia.",
        "visual": "Cluster atom donor & akseptor dengan panah lengkung dan elektron e- bergerak.",
    },
    "polymer_crosslink": {
        "name": "Ikatan Silang Polimer / Rigiditas Material",
        "when_to_use": "HANYA untuk polimer, hidrogel, elastisitas bahan ditekuk, penguatan serat atau plastik.",
        "visual": "Rantai node polimer melengkung saat ditekan lalu terkunci oleh jembatan silang.",
    },
    "molecular_reaction": {
        "name": "Tumbukan & Pembentukan Ikatan Molekul",
        "when_to_use": "Dua senyawa bereaksi membentuk senyawa kimia baru (reaksi sintesis/kovalen umum).",
        "visual": "Dua gugus molekul bola-tongkat mendekat lalu membentuk ikatan ganda baru.",
    },
    "receptor_binding": {
        "name": "Ikatan Reseptor / Sinyal Saraf (Lock & Key)",
        "when_to_use": "HANYA untuk obat menempel di reseptor nyeri, hormon, kafein di adenosin, virus ke sel.",
        "visual": "Ligan masuk presisi ke kantong aktif reseptor target.",
    },
    "force_wave": {
        "name": "Gelombang Energi, Cahaya, Suara & Gaya Fisika",
        "when_to_use": "Gelombang elektromagnetik, UV, suara/akustik, konduksi getaran panas, gaya tarik.",
        "visual": "Gelombang sinus berjalan dengan partikel berosilasi dan vektor gaya.",
    },
    "math_geometry": {
        "name": "Kurva Matematika, Peluang & Pola Eksponensial",
        "when_to_use": "Grafik pertumbuhan cepat, kurva probabilitas, perbandingan rasio, pola geometri.",
        "visual": "Koordinat grafik dengan kurva hidup dan titik pelacak bergerak.",
    },
    "osmosis_membrane": {
        "name": "Osmosis & Perpindahan Air Menembus Membran Sel",
        "when_to_use": "Jari keriput berendam, osmosis garam/gula, sel mengembang/mengerut, kadar air.",
        "visual": "Membran bilayer berpori dengan aliran molekul air ke sisi konsentrasi pekat.",
    },
    "protein_cleavage": {
        "name": "Pemotongan Rantai Protein / Enzim Protease",
        "when_to_use": "Nanas melunakkan daging (bromelain), enzim pencerna makanan, pemecahan serat/polimer.",
        "visual": "Enzim pemotong bergerak menyusuri rantai protein panjang dan memutus ikatan jadi fragmen.",
    },
    "phase_transition": {
        "name": "Transisi Fase & Ekspansi Kalor (Padat/Cair/Uap)",
        "when_to_use": "Air meledak di minyak panas 180C, es mencair, pemuaian uap, karamelisasi gula panas.",
        "visual": "Termometer aktif bersama partikel yang bertransisi dari rapat hingga memuai jadi uap.",
    },
    "surface_surfactant": {
        "name": "Surfaktan, Tegangan Permukaan & Emulsi Minyak-Air",
        "when_to_use": "Sabun mengangkat lemak, minyak tidak campur air, misel deterjen, tegangan permukaan.",
        "visual": "Molekul surfaktan berpolar ganda mengelilingi tetes minyak lalu mengangkatnya lepas.",
    },
    "acid_base_bubble": {
        "name": "Reaksi Asam-Basa & Pelepasan Gelembung Gas",
        "when_to_use": "Cuka melarutkan kerak/baking soda, korosi asam lambung, reaksi berbusa CO2/H2.",
        "visual": "Tetes asam menghantam lapisan basa/logam menghasilkan puluhan gelembung gas naik.",
    },
    "crystal_lattice": {
        "name": "Pertumbuhan / Retaknya Kisi Kristal Teratur",
        "when_to_use": "Pembentukan kristal es, depresi titik beku garam+es, kristal gula/garam, kisi logam.",
        "visual": "Kisi kristal simetris tumbuh node demi node atau retak saat tersisipi ion asing.",
    },
}

DEFAULT_CUSTOM_PRESETS: list[dict[str, Any]] = [
    {
        "preset_id": "bromelain_meat_tenderizer",
        "label_title": "PEMOTONGAN SERAT PROTEIN",
        "engine_kind": "protein_cleavage",
        "accent_color": "amber",
        "trigger_keywords": ["nanas", "bromelain", "daging", "protease", "empuk", "kolagen"],
        "default_actor_a": "Enzim Bromelain",
        "default_actor_b": "Serat Kolagen",
        "default_result": "Protein Terpotong",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "skin_osmosis_wrinkle",
        "label_title": "OSMOSIS & SARAF PEMBULUH",
        "engine_kind": "osmosis_membrane",
        "accent_color": "cyan",
        "trigger_keywords": ["keriput", "berendam", "osmosis", "membran", "kulit", "garam"],
        "default_actor_a": "Air Luar (H₂O)",
        "default_actor_b": "Lapisan Epidermis",
        "default_result": "Lipatan Cengkeram",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "flash_steam_oil_burst",
        "label_title": "UAP AIR MEMUAI 1600X",
        "engine_kind": "phase_transition",
        "accent_color": "red",
        "trigger_keywords": ["minyak", "panas", "meledak", "mendidih", "uap", "kalor"],
        "default_actor_a": "Air (100°C)",
        "default_actor_b": "Minyak (180°C)",
        "default_result": "Ledakan Uap 1600x",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
    {
        "preset_id": "soap_micelle_lift",
        "label_title": "MISEL SABUN ANGKAT LEMAK",
        "engine_kind": "surface_surfactant",
        "accent_color": "blue",
        "trigger_keywords": ["sabun", "deterjen", "lemak", "emulsi", "surfaktan", "bilas"],
        "default_actor_a": "Surfaktan Sabun",
        "default_actor_b": "Lapisan Minyak",
        "default_result": "Misel Terbilas Air",
        "created_by": "seed_bank",
        "created_week": "2026-11-26",
    },
]


def _default_bank_payload() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "core_engines": BUILTIN_MECHANISM_ENGINES,
        "custom_presets": list(DEFAULT_CUSTOM_PRESETS),
    }


def load_mechanism_bank(bank_path: Path = BANK_PATH) -> dict[str, Any]:
    """Load `data/mechanism_bank.json`, initializing it cleanly if absent."""
    if not bank_path.exists():
        payload = _default_bank_payload()
        try:
            bank_path.parent.mkdir(parents=True, exist_ok=True)
            bank_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return payload
    try:
        data = json.loads(bank_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_bank_payload()
        data.setdefault("core_engines", BUILTIN_MECHANISM_ENGINES)
        data.setdefault("custom_presets", [])
        return data
    except Exception:
        return _default_bank_payload()


def format_mechanism_catalog_for_agent3(bank_path: Path = BANK_PATH) -> dict[str, Any]:
    """Compact bank digest passed to Agent 3 so script/storyboard picks rich, varied engines."""
    bank = load_mechanism_bank(bank_path)
    engines = {
        k: v.get("when_to_use", "")
        for k, v in (bank.get("core_engines") or BUILTIN_MECHANISM_ENGINES).items()
    }
    presets = [
        {
            "preset_id": p.get("preset_id"),
            "engine_kind": p.get("engine_kind"),
            "label_title": p.get("label_title"),
            "trigger_keywords": p.get("trigger_keywords", [])[:6],
            "default_actors": f"{p.get('default_actor_a')} -> {p.get('default_actor_b')} -> {p.get('default_result')}",
        }
        for p in (bank.get("custom_presets") or [])[-20:]
        if p.get("preset_id") and p.get("engine_kind") in BUILTIN_MECHANISM_ENGINES
    ]
    return {"core_engines": engines, "custom_presets": presets}


def resolve_preset_style(preset_id: Optional[str], bank_path: Path = BANK_PATH) -> dict[str, Any]:
    """Return styling metadata (accent_color / engine_kind fallback) for a matching preset_id."""
    if not preset_id:
        return {}
    pid = preset_id.strip().lower()
    bank = load_mechanism_bank(bank_path)
    for preset in bank.get("custom_presets") or []:
        if str(preset.get("preset_id") or "").strip().lower() == pid:
            return dict(preset)
    return {}


def append_mechanism_presets_from_audit(
    proposals: list[Any],
    week_ending: str,
    bank_path: Path = BANK_PATH,
) -> list[dict[str, Any]]:
    """Persist new mechanism bank presets proposed by Agent 8 (deduplicated & validated)."""
    if not proposals:
        return []
    bank = load_mechanism_bank(bank_path)
    existing: list[dict[str, Any]] = list(bank.get("custom_presets") or [])
    by_id = {str(p.get("preset_id") or "").strip().lower(): p for p in existing if p.get("preset_id")}
    added: list[dict[str, Any]] = []

    for prop in proposals:
        raw = prop.model_dump() if hasattr(prop, "model_dump") else dict(prop)
        slug = re.sub(r"[^a-z0-9_]+", "_", str(raw.get("preset_id") or "").strip().lower()).strip("_")
        engine_kind = str(raw.get("engine_kind") or "").strip()
        if not slug or engine_kind not in BUILTIN_MECHANISM_ENGINES:
            continue
        if slug in by_id:
            continue
        entry = {
            "preset_id": slug,
            "label_title": str(raw.get("label_title") or "MEKANISME SAINS").strip()[:34].upper(),
            "engine_kind": engine_kind,
            "accent_color": str(raw.get("accent_color") or "cyan").strip().lower(),
            "trigger_keywords": [
                str(k).strip().lower()
                for k in (raw.get("trigger_keywords") or [])
                if str(k).strip()
            ][:8],
            "default_actor_a": str(raw.get("default_actor_a") or "A").strip()[:16],
            "default_actor_b": str(raw.get("default_actor_b") or "B").strip()[:16],
            "default_result": str(raw.get("default_result") or "Hasil").strip()[:22],
            "rationale": str(raw.get("rationale") or "").strip()[:140],
            "created_by": "agent8_weekly_audit",
            "created_week": week_ending,
        }
        existing.append(entry)
        by_id[slug] = entry
        added.append(entry)

    if added:
        bank["core_engines"] = BUILTIN_MECHANISM_ENGINES
        bank["custom_presets"] = existing[-80:]
        bank_path.parent.mkdir(parents=True, exist_ok=True)
        bank_path.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
    return added
