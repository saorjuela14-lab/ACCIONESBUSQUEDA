"""Versiones vigentes de los documentos legales (Ley 1581/2012, Decreto 1377/2013).

Cambiar la versión cuando se modifique el texto de /privacidad o /terminos: la prueba
de consentimiento guarda la versión aceptada por cada titular.
"""

PRIVACY_POLICY_VERSION = "privacidad-v1.0-2026-10-20"
TERMS_VERSION = "terminos-v1.0-2026-10-20"

# Acciones en audit_events (tabla existente, payload_json) — sin migración de esquema.
AUDIT_ACTION_CONSENT = "legal_consent"
AUDIT_ACTION_DATA_REQUEST = "habeas_data_request"
