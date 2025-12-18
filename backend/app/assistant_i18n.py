from __future__ import annotations

from typing import Mapping

from .i18n import DEFAULT_LOCALE, format_template, normalize_locale, t


CONVERSATION_STRINGS: Mapping[str, Mapping[str, str]] = {
    "en": {
        "handoff_base": (
            "To be safe, I'll hand this off to the team for a quick call back. "
            "You can also leave a short voicemail with your name and address."
        ),
        "handoff_emergency_append": " If this is life-threatening, hang up and call 911.",
        "emergency_confirm": (
            "It sounds like this might be an emergency ({reason}). "
            "Is this an emergency? Please reply YES or NO."
        ),
        "greeting_returning": (
            "Hi{name_part}, this is the automated assistant for {business_name}. "
            "It looks like we've worked with you before. "
            "To confirm our records, what name should I put on this visit? "
        ),
        "greeting_new": (
            "Hi, this is the automated assistant for {business_name}. "
            "I can help you schedule a {vertical} visit. "
            "To get started, what is your name? "
        ),
        "ask_name_missing": "Sorry, I didn't catch your name. What is your name?",
        "ask_address_after_greeting": (
            "Thanks, {name}. What is the service address for this visit?"
        ),
        "ask_address_after_name": "Okay, what is the service address for this visit?",
        "offer_existing_address": (
            "I have your address as {address}. Does that still work for this visit?"
        ),
        "ask_address_full": "What is the full service address for this visit?",
        "ask_problem": "Got it. Briefly describe what's going on with your {vertical}.",
        "ask_problem_missing": (
            "Please describe the {vertical} issue so we know what to prepare for."
        ),
        "schedule_prefix_emergency": (
            "Thanks, that sounds urgent. I'll flag this as an emergency job. "
            "I cannot contact emergency services for you, so if this is life-threatening, "
            "hang up and call 911 or your local emergency number. "
        ),
        "schedule_prefix_standard": "Thanks for the details. ",
        "schedule_question": (
            "Would you like me to look for the next available appointment time?"
        ),
        "schedule_decline": (
            "Okay, I won't schedule anything right now. "
            "Someone from {business_name} will follow up with you."
        ),
        "schedule_need_address": (
            "Before I look for times, I need the full service address for this visit."
        ),
        "schedule_no_slot": (
            "I'm unable to find an open time slot right now. "
            "Someone will review your request and call you back shortly."
        ),
        "schedule_propose": "I can book you for {when}. Does that time work for you?",
        "confirm_slot_decline": (
            "Okay, I won't schedule that time. "
            "A team member will contact you to find a different slot."
        ),
        "confirm_slot_unable": (
            "I'm unable to confirm a time slot right now. "
            "Someone will review your request and call you back shortly."
        ),
        "customer_sms_confirm": (
            "This is {business_name}. Your appointment is scheduled for {when}.\n"
            "If this time does not work, please call or text to reschedule."
        ),
        "completed_standard": (
            "You're all set. We've scheduled your appointment and will see you then."
        ),
        "completed_emergency_append": (
            " Because this was flagged as an emergency, we will treat it as a high priority."
        ),
        "completed_fallback": (
            "This session looks complete. If you need anything else, please call back."
        ),
    },
    "es": {
        "handoff_base": (
            "Para estar seguros, pasaré esto al equipo para que te llame pronto. "
            "Si prefieres, puedes dejar un breve buzón de voz con tu nombre y dirección."
        ),
        "handoff_emergency_append": (
            " Si se trata de una emergencia que pone en riesgo la vida, llama al 911 o a tu número local."
        ),
        "emergency_confirm": (
            "Parece que esto podría ser una emergencia ({reason}). "
            "¿Es una emergencia? Por favor responde SÍ o NO."
        ),
        "greeting_returning": (
            "Hola{name_part}, te habla el asistente automatizado de {business_name}. "
            "Parece que ya hemos trabajado contigo antes. "
            "Para confirmar nuestros registros, ¿qué nombre debo poner para esta visita? "
        ),
        "greeting_new": (
            "Hola, te habla el asistente automatizado de {business_name}. "
            "Puedo ayudarte a programar una visita de {vertical}. "
            "Para empezar, ¿cuál es tu nombre? "
        ),
        "ask_name_missing": "No alcancé a escuchar tu nombre. ¿Cómo te llamas?",
        "ask_address_after_greeting": (
            "Gracias, {name}. ¿Cuál es la dirección del servicio para esta visita?"
        ),
        "ask_address_after_name": "De acuerdo, ¿cuál es la dirección del servicio para esta visita?",
        "offer_existing_address": (
            "Tengo tu dirección como {address}. ¿Sigue siendo correcta para esta visita?"
        ),
        "ask_address_full": "¿Cuál es la dirección completa del servicio para esta visita?",
        "ask_problem": "Perfecto. Describe brevemente qué está pasando con la {vertical}.",
        "ask_problem_missing": (
            "Por favor describe el problema de {vertical} para saber cómo prepararnos."
        ),
        "schedule_prefix_emergency": (
            "Gracias, eso suena urgente. Marcaré esto como un trabajo de emergencia. "
            "No puedo contactar a los servicios de emergencia por ti, así que si se trata de una "
            "situación que pone en riesgo la vida, cuelga y llama al 911 o a tu número de emergencias local. "
        ),
        "schedule_prefix_standard": "Gracias por los detalles. ",
        "schedule_question": "¿Quieres que busque la siguiente cita disponible?",
        "schedule_decline": (
            "De acuerdo, no agendaré nada por ahora. "
            "Alguien de {business_name} se pondrá en contacto contigo."
        ),
        "schedule_need_address": (
            "Antes de buscar horarios, necesito la dirección completa del servicio para esta visita."
        ),
        "schedule_no_slot": (
            "No puedo encontrar un horario disponible en este momento. "
            "Alguien revisará tu solicitud y te llamará pronto."
        ),
        "schedule_propose": "Te puedo agendar el {when}. ¿Ese horario te funciona?",
        "confirm_slot_decline": (
            "De acuerdo, no reservaré ese horario. "
            "Un miembro del equipo se pondrá en contacto contigo para encontrar otra hora."
        ),
        "confirm_slot_unable": (
            "No puedo confirmar un horario en este momento. "
            "Alguien revisará tu solicitud y te llamará pronto."
        ),
        "customer_sms_confirm": (
            "Habla {business_name}. Tu cita está programada para el {when}.\n"
            "Si ese horario no te funciona, por favor llama o envíanos un mensaje de texto para reprogramar."
        ),
        "completed_standard": "Listo. Hemos programado tu cita y te veremos entonces.",
        "completed_emergency_append": (
            " Como fue marcada como emergencia, la trataremos como una prioridad alta."
        ),
        "completed_fallback": (
            "Esta sesión parece completa. Si necesitas algo más, por favor vuelve a llamar."
        ),
    },
}


def conversation_text(language_code: str | None, key: str, **variables: object) -> str:
    locale = normalize_locale(language_code)
    template = t(CONVERSATION_STRINGS, locale, key)
    return format_template(template, variables)


def conversation_locale(language_code: str | None) -> str:
    locale = normalize_locale(language_code)
    return locale if locale in CONVERSATION_STRINGS else DEFAULT_LOCALE
