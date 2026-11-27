#!/usr/bin/env python3
"""Chiffrement des jetons My Page (dépôt public : jamais de secret dans les messages d'erreur).

1. Jeton reçu de l'appli (champ tokenEnc) : JSON {v, alg, ek, iv, ct, tag} en base64.
   AES-256-GCM (nonce 12 octets, tag 16 octets, sans données associées) ; la clé AES est
   chiffrée avec RSA-OAEP (SHA-256, MGF1 SHA-256). Déchiffré avec MYPAGE_RSA_PRIVATE_KEY.
2. Jeton de Page conservé par le bot : AES-256-GCM avec MYPAGE_STORE_KEY (32 octets en
   base64). L'identité de la Page est liée au chiffrement (données associées) : un jeton
   recopié vers un autre document ne se déchiffre plus.
Dépendance : cryptography.
"""
import base64
import binascii
import json
import os
import re

ALG = "RSA-OAEP-256+A256GCM"
TAILLE_MAX_ENC = 8192      # même limite que firestore.rules
TAILLE_MAX_JETON = 4096
PREFIXE_STOCKAGE = "v1"


class ErreurChiffrement(ValueError):
    """Message fixe et court : jamais de contenu, de clé ni de jeton."""


def _b64(valeur, taille=None, nom="champ"):
    if not isinstance(valeur, str) or not valeur:
        raise ErreurChiffrement(f"{nom} invalide")
    try:
        octets = base64.b64decode(valeur, validate=True)
    except (binascii.Error, ValueError):
        raise ErreurChiffrement(f"{nom} invalide") from None
    if taille is not None and len(octets) != taille:
        raise ErreurChiffrement(f"{nom} invalide")
    return octets


# ---------- jeton envoyé par l'appli ----------
def charger_cle_privee(brut):
    """Clé privée RSA depuis le secret : PEM brut, PEM avec \\n littéraux, ou PEM en base64."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    texte = (brut or "").strip().replace("\\n", "\n")
    if not texte:
        raise ErreurChiffrement("clé privée absente")
    if "-----BEGIN" not in texte:
        try:
            texte = base64.b64decode(texte, validate=True).decode("utf-8")
        except Exception:
            raise ErreurChiffrement("clé privée illisible") from None
    try:
        cle = serialization.load_pem_private_key(texte.encode("utf-8"), password=None)
    except Exception:
        raise ErreurChiffrement("clé privée illisible") from None
    if not isinstance(cle, rsa.RSAPrivateKey) or cle.key_size < 2048:
        raise ErreurChiffrement("clé privée RSA de 2048 bits minimum requise")
    return cle


def dechiffrer_jeton(token_enc, cle_privee):
    """Déchiffre le champ tokenEnc de l'appli et renvoie le jeton en clair."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not isinstance(token_enc, str) or not token_enc or len(token_enc) > TAILLE_MAX_ENC:
        raise ErreurChiffrement("jeton chiffré absent ou trop long")
    try:
        doc = json.loads(token_enc)
    except ValueError:
        raise ErreurChiffrement("jeton chiffré illisible") from None
    if not isinstance(doc, dict) or doc.get("v") != 1 or doc.get("alg") != ALG:
        raise ErreurChiffrement("format de chiffrement non pris en charge")
    ek = _b64(doc.get("ek"), cle_privee.key_size // 8, "clé chiffrée")
    iv = _b64(doc.get("iv"), 12, "nonce")
    tag = _b64(doc.get("tag"), 16, "tag")
    ct = _b64(doc.get("ct"), None, "texte chiffré")
    if len(ct) > TAILLE_MAX_JETON:
        raise ErreurChiffrement("texte chiffré trop long")
    try:
        cle_aes = cle_privee.decrypt(ek, padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        clair = AESGCM(cle_aes).decrypt(iv, ct + tag, None).decode("utf-8")
    except Exception:
        raise ErreurChiffrement("déchiffrement impossible") from None
    jeton = clair.strip()
    if not jeton or not re.fullmatch(r"[\x21-\x7e]+", jeton):
        raise ErreurChiffrement("jeton invalide")
    return jeton


# ---------- stockage réservé au bot ----------
def charger_cle_stockage(brut):
    """MYPAGE_STORE_KEY : 32 octets en base64 (standard ou URL-safe)."""
    texte = (brut or "").strip().replace("-", "+").replace("_", "/")
    if not texte:
        raise ErreurChiffrement("clé de stockage absente")
    try:
        octets = base64.b64decode(texte + "=" * (-len(texte) % 4), validate=True)
    except (binascii.Error, ValueError):
        raise ErreurChiffrement("clé de stockage illisible") from None
    if len(octets) != 32:
        raise ErreurChiffrement("clé de stockage : 32 octets requis")
    return octets


def _contexte(uid, page_id):
    return f"{uid}/{page_id}".encode("utf-8")


def chiffrer_stockage(jeton, cle, uid, page_id):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not isinstance(jeton, str) or not jeton:
        raise ErreurChiffrement("jeton à stocker absent")
    iv = os.urandom(12)
    ct = AESGCM(cle).encrypt(iv, jeton.encode("utf-8"), _contexte(uid, page_id))
    return (f"{PREFIXE_STOCKAGE}." + base64.b64encode(iv).decode("ascii")
            + "." + base64.b64encode(ct).decode("ascii"))


def dechiffrer_stockage(valeur, cle, uid, page_id):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    morceaux = valeur.split(".") if isinstance(valeur, str) else []
    if len(morceaux) != 3 or morceaux[0] != PREFIXE_STOCKAGE:
        raise ErreurChiffrement("jeton stocké illisible")
    iv = _b64(morceaux[1], 12, "nonce")
    ct = _b64(morceaux[2], None, "texte chiffré")
    try:
        return AESGCM(cle).decrypt(iv, ct, _contexte(uid, page_id)).decode("utf-8")
    except Exception:
        raise ErreurChiffrement("jeton stocké : déchiffrement impossible") from None


# ---------- journaux ----------
def masquer(valeur):
    """Demande à GitHub Actions de masquer la valeur dans tous les logs suivants."""
    if os.environ.get("GITHUB_ACTIONS") == "true" and isinstance(valeur, str) \
            and valeur and "\n" not in valeur and "\r" not in valeur:
        print(f"::add-mask::{valeur}", flush=True)
