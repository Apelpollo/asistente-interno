"""Preguntas para evaluar SOLO el retrieval (sin LLM).

Cada caso dice qué texto ("aguja") tiene que aparecer en algún chunk recuperado
para que el LLM pueda responder bien, y de qué archivo viene.
Las preguntas están parafraseadas a propósito: no repiten las palabras del documento,
así se mide búsqueda semántica y no coincidencia literal.

Si cambias los documentos de docs/, revisa que cada aguja siga existiendo
(tune_retrieval.py lo valida al arrancar).
"""

CASOS = [
    # ---------- Manual de Calidad MQ-01 ----------
    {"pregunta": "¿Qué tolerancia de presión no invasiva deben cumplir los monitores?",
     "aguja": "3 mmhg", "archivo": "manual_calidad.md"},
    {"pregunta": "¿Cuánto debe durar como mínimo la batería de un monitor de signos vitales?",
     "aguja": "autonomia minima de 4 horas", "archivo": "manual_calidad.md"},
    {"pregunta": "¿En qué rango de saturación se valida la precisión de los oxímetros?",
     "aguja": "70% a 100%", "archivo": "manual_calidad.md"},
    {"pregunta": "¿A qué velocidades se prueba el flujo de las bombas de infusión?",
     "aguja": "10, 100 y 500 ml/h", "archivo": "manual_calidad.md"},
    {"pregunta": "¿Qué pruebas se le hacen a los cables de paciente antes de liberarlos?",
     "aguja": "prueba de jalon", "archivo": "manual_calidad.md"},
    {"pregunta": "¿Hasta qué tamaño de orden se revisan todas las piezas sin muestreo?",
     "aguja": "50 unidades o menos", "archivo": "manual_calidad.md"},
    {"pregunta": "¿Qué nivel de calidad aceptable se usa en el muestreo de defectos mayores?",
     "aguja": "0.65", "archivo": "manual_calidad.md"},
    {"pregunta": "¿Cómo se clasifica un conector con una soldadura defectuosa?",
     "aguja": "soldadura fria en un conector", "archivo": "manual_calidad.md"},
    {"pregunta": "¿Se puede corregir en la misma línea un defecto que pone en riesgo al paciente?",
     "aguja": "no se retrabajan en linea", "archivo": "manual_calidad.md"},
    {"pregunta": "¿Qué margen de error se permite en la energía de un desfibrilador?",
     "aguja": "10% en cada nivel", "archivo": "manual_calidad.md"},

    # ---------- Política de Garantías PG-03 ----------
    {"pregunta": "¿Cuánto cubre la garantía de la batería de un desfibrilador?",
     "aguja": "12 meses de garantia por separado", "archivo": "politica_garantias.md"},
    {"pregunta": "¿A partir de qué fecha empieza a correr la garantía?",
     "aguja": "fecha de entrega al cliente", "archivo": "politica_garantias.md"},
    {"pregunta": "Si a un equipo se le derrama agua, ¿lo cubre la garantía?",
     "aguja": "liquidos", "archivo": "politica_garantias.md"},
    {"pregunta": "¿Qué tan rápido se manda un equipo sustituto a un hospital?",
     "aguja": "48 horas", "archivo": "politica_garantias.md"},
    {"pregunta": "¿Cuántas quejas por la misma falla hacen que Calidad considere retener producción?",
     "aguja": "tres o mas reclamaciones", "archivo": "politica_garantias.md"},
    {"pregunta": "¿En cuánto tiempo se arregla o cambia el equipo que falló en garantía?",
     "aguja": "se repara o reemplaza", "archivo": "politica_garantias.md"},

    # ---------- Procedimiento PR-07 ----------
    {"pregunta": "¿Con qué porcentaje de piezas rechazadas se detiene una orden aunque los defectos no sean críticos?",
     "aguja": "supera el 10%", "archivo": "procedimiento_retencion.md"},
    {"pregunta": "¿A dónde se llevan las unidades de una orden detenida?",
     "aguja": "cuarentena", "archivo": "procedimiento_retencion.md"},
    {"pregunta": "¿Cuánto tiempo tiene Ingeniería para encontrar la causa del problema?",
     "aguja": "causa raiz en un maximo de 5 dias habiles", "archivo": "procedimiento_retencion.md"},
    {"pregunta": "¿Cuándo conviene tirar una orden retenida en lugar de repararla?",
     "aguja": "60%", "archivo": "procedimiento_retencion.md"},
    {"pregunta": "¿Quién autoriza que una orden siga detenida más allá del plazo máximo?",
     "aguja": "gerencia de planta", "archivo": "procedimiento_retencion.md"},
    {"pregunta": "¿Qué se le ofrece a un hospital si su pedido se atrasa por una orden detenida?",
     "aguja": "fecha de entrega alternativa", "archivo": "procedimiento_retencion.md"},
]
