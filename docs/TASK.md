# Enunciado del ejercicio técnico (texto original)

> Copia literal del enunciado recibido para la entrevista técnica. No editar: es la fuente de verdad de los requerimientos.

---

1. Objetivo


Desarrollar un agente conversacional capaz de interpretar solicitudes de usuarios, identificar las acciones que necesitan realizarse y orquestar dinámicamente llamadas a herramientas (Tools).

El agente deberá ser capaz de:

1. Interpretar la intención del usuario mediante lenguaje natural.

2. Identificar qué herramienta o herramientas necesita utilizar.
3. Extraer los argumentos necesarios para ejecutar cada herramienta.
4. Validar los argumentos contra el contrato de cada herramienta.
5. Identificar argumentos faltantes, inválidos o ambiguos.
6. Solicitar al usuario la información faltante mediante preguntas conversacionales.
7. Mantener el contexto y los argumentos recopilados entre diferentes interacciones.
8. Ejecutar las herramientas únicamente cuando se cumplan sus condiciones de ejecución.
9. Interpretar los resultados de las herramientas y proporcionar feedback al usuario.
10. Manejar errores, dependencias entre herramientas y posibles ejecuciones duplicadas.

El objetivo no es construir un chatbot que simplemente invoque funciones. Se busca implementar un agente que controle un ciclo completo de interpretación, validación, interacción y ejecución.

2. Contexto del problema

Se requiere desarrollar un asistente empresarial que permita realizar operaciones mediante lenguaje natural.

El asistente tendrá acceso a las siguientes herramientas:

- "create_ticket": creación de tickets de soporte.

- "schedule_meeting": programación de reuniones.

- "send_notification": envío de notificaciones.


Las herramientas serán simuladas (mock). No es necesario implementar integraciones con servicios externos.

2.1. Definición de Tools

Tool 1: create_ticket

Permite crear un ticket de soporte.

Campo| Tipo| Obligatorio
title| string| Sí
description| string| Sí
priority| low / medium / high| Sí
assignee| string| Sí

Respuesta simulada:

- success

- ticket_id

- message


Tool 2: schedule_meeting

Permite programar una reunión.

Campo| Tipo| Obligatorio
title| string| Sí
date| YYYY-MM-DD| Sí
time| HH:mm| Sí
duration_minutes| integer| Sí
attendees| string[]| Sí

Respuesta simulada:

- success

- meeting_id

- message


Tool 3: send_notification

Permite enviar una notificación.

Campo| Tipo| Obligatorio
recipient| string| Sí
message| string| Sí
channel| email / sms / push| Sí

Respuesta simulada:

- success

- notification_id

- message


3. Requerimientos funcionales

RF-01. Identificación de intención

El agente debe identificar la intención principal del usuario y determinar si corresponde a una o varias herramientas disponibles.

No debe depender exclusivamente de palabras clave.

Ejemplos:

- "Necesito reportar que el servidor está caído" → create_ticket.

- "Agenda una reunión con Juan" → schedule_meeting.

- "Avísale a Carlos que el servidor ya está disponible" → send_notification.


Debe ser capaz de identificar solicitudes que impliquen varias operaciones.

RF-02. Selección de herramientas

El agente debe seleccionar las herramientas correspondientes a la intención identificada.

Debe evitar:

- Invocar herramientas que no correspondan a la intención.

- Ejecutar acciones por asociaciones semánticas que no representan una solicitud real.

- Ejecutar herramientas innecesarias.

- Asumir que una intención ambigua implica autorización para ejecutar varias acciones.


Cuando no exista suficiente claridad, deberá solicitar una aclaración.

RF-03. Extracción de argumentos

El agente debe extraer los argumentos de las herramientas utilizando la información proporcionada por el usuario.

Los argumentos pueden distribuirse entre múltiples mensajes.

Debe mantener los valores previamente recopilados y actualizarlos cuando el usuario proporcione información nueva.

RF-04. Validación de argumentos

Cada herramienta debe contar con un contrato de argumentos claramente definido.

El agente debe validar:

- Presencia de campos obligatorios.

- Tipos de datos.

- Valores permitidos.

- Formatos.

- Restricciones de negocio.

- Coherencia entre argumentos.


La validación debe ser determinística.

El LLM no debe ser responsable de garantizar por sí mismo la validez de los argumentos.

RF-05. Manejo de argumentos faltantes

Cuando una herramienta requiera argumentos que no fueron proporcionados, el agente debe:

1. Identificar los campos faltantes.

2. Determinar qué información necesita solicitar.
3. Generar una pregunta conversacional clara.
4. Esperar la respuesta del usuario.
5. Incorporar la nueva información al contexto.
6. Volver a validar los argumentos.

No debe ejecutar una herramienta hasta cumplir sus condiciones de validación.

No debe inventar información para completar argumentos.

RF-06. Manejo de argumentos inválidos

Si el usuario proporciona información incorrecta, el agente debe identificar el problema y solicitar una corrección.

Ejemplo:

Usuario:

"Agenda la reunión el 40 de noviembre a las 25:90".

Respuesta esperada:

"Hay un problema con la fecha y la hora. La fecha indicada no existe y la hora está fuera del formato válido. ¿Qué fecha y hora deseas utilizar?"

No debe ejecutar la herramienta.

RF-07. Manejo de contexto y memoria

El agente debe mantener el contexto conversacional necesario para completar operaciones pendientes.

El candidato tendrá libertad para seleccionar el mecanismo de memoria que considere más apropiado.

Debe poder mantener:

- Identificación de la intención actual.

- Herramienta o herramientas pendientes.

- Argumentos recopilados.

- Argumentos faltantes.

- Estado de validación.

- Estado de ejecución.

- Resultados de las herramientas.

- Dependencias entre operaciones cuando existan.


El candidato deberá justificar su elección de memoria y explicar cómo garantiza la continuidad del estado conversacional.

Es importante diferenciar el historial conversacional de la información estructurada necesaria para controlar el ciclo de ejecución.

RF-08. Ejecución de herramientas

Las herramientas únicamente deben ejecutarse cuando:

- La intención haya sido identificada.

- La herramienta corresponda a la intención.

- Los argumentos obligatorios estén completos.

- Los argumentos hayan sido validados.

- Se cumplan las condiciones de autorización necesarias.


El agente debe interpretar los resultados y proporcionar feedback al usuario.

RF-09. Orquestación de múltiples herramientas

El agente debe soportar solicitudes que requieran ejecutar más de una herramienta.

Debe ser capaz de identificar dependencias y establecer un orden de ejecución.

Ejemplo:

"Crea un ticket de prioridad alta para Carlos y mándale una notificación por email cuando se haya creado".

Flujo esperado:

1. Identificar create_ticket y send_notification.

2. Extraer los argumentos disponibles de ambas operaciones.
3. Solicitar los argumentos faltantes.
4. Validar los argumentos.
5. Ejecutar create_ticket.
6. Recuperar el ticket_id generado.
7. Ejecutar send_notification utilizando el resultado de la primera operación.
8. Informar el resultado de ambas operaciones.

Si la primera operación falla, no debe ejecutar la segunda.

Debe explicar cómo manejaría fallos parciales y reintentos.

RF-10. Seguridad y control de ejecución

El agente debe distinguir entre una solicitud, una propuesta y una autorización para ejecutar una acción.

Ejemplo:

El usuario solicita programar una reunión, pero el agente necesita confirmar información antes de ejecutarla.

Un "sí, hazlo" debe interpretarse dentro del contexto de una operación pendiente y claramente identificada.

No debe utilizarse como autorización genérica para ejecutar otras herramientas.

También debe evitar ejecuciones duplicadas ante reintentos o mensajes repetidos.

4. Casos de prueba obligatorios

La implementación debe cubrir los siguientes escenarios.

Caso A. Intención clara con argumentos incompletos

Usuario:

"Necesito crear un ticket porque el servidor está caído".

Comportamiento esperado:

- Identificar create_ticket.

- Extraer la descripción.

- Detectar title, priority y assignee como campos faltantes.

- Solicitar la información faltante.

- No ejecutar la herramienta todavía.


Caso B. Argumentos distribuidos entre mensajes

Usuario:

"Crea un ticket para revisar el problema de autenticación".

Agente:

"¿Qué prioridad tendrá y a quién se lo asignamos?"

Usuario:

"Alta, asígnalo a Carlos".

Comportamiento esperado:

- Mantener la intención original.

- Conservar la descripción.

- Incorporar los nuevos argumentos.

- Validar nuevamente.

- Ejecutar únicamente cuando todos los campos estén completos.


Caso C. Intención ambigua

Usuario:

"Avísale a Carlos que mañana revisamos el servidor".

El agente debe determinar si se refiere a:

- Enviar una notificación.

- Programar una reunión.

- Ambas operaciones.


No debe ejecutar varias herramientas solamente por asociación semántica.

Debe solicitar una aclaración si no existe suficiente información para determinar la intención.

Caso D. Argumentos inválidos

Usuario:

"Agenda una reunión el 40 de noviembre a las 25:90".

Comportamiento esperado:

- Identificar schedule_meeting.

- Detectar los argumentos inválidos.

- Solicitar correcciones.

- No ejecutar la herramienta.


Caso E. Múltiples herramientas con dependencia

Usuario:

"Crea un ticket de prioridad alta para Carlos y mándale una notificación por email cuando esté creado".

Comportamiento esperado:

- Identificar ambas herramientas.

- Extraer sus argumentos.

- Solicitar los campos faltantes.

- Ejecutar las herramientas en el orden correcto.

- Transferir el ticket_id entre herramientas.

- Informar el resultado de ambas operaciones.


Caso F. Cambio de intención

Usuario:

"Agenda una reunión con Juan".

Agente:

"¿Qué día y a qué hora?"

Usuario:

"Mejor crea un ticket para revisar el servidor".

Comportamiento esperado:

- Detectar el cambio de intención.

- No mezclar los argumentos de ambas operaciones.

- No ejecutar la reunión incompleta.

- Iniciar el nuevo flujo de creación de ticket.


Caso G. Confirmación de ejecución

Usuario:

"Envía un correo a Carlos para informarle que el ticket quedó resuelto".

El agente recopila los argumentos necesarios y solicita confirmación antes de ejecutar.

Usuario:

"Sí, hazlo".

Comportamiento esperado:

- Asociar la confirmación a la operación pendiente.

- Ejecutar únicamente la herramienta correspondiente.

- No solicitar nuevamente información que ya fue proporcionada.


Caso H. Repetición de mensajes

El usuario envía dos veces la misma solicitud de creación de ticket o se produce un reintento después de un timeout.

Comportamiento esperado:

- Evitar crear operaciones duplicadas cuando corresponda a la misma solicitud lógica.

- Implementar un mecanismo de idempotencia.

- Explicar cómo se comportaría el sistema cuando no pueda determinar si una ejecución anterior terminó correctamente.


5. Requerimientos tecnológicos

Tecnologías obligatorias

- Python.

- Google Agent Development Kit (ADK).

- Un modelo compatible con ADK.

- Git y GitHub.


Tecnologías opcionales

El candidato podrá seleccionar libremente las tecnologías adicionales que considere apropiadas para resolver el problema.

Deberá justificar las decisiones tecnológicas relevantes, especialmente las relacionadas con memoria, persistencia, validación, ejecución y observabilidad.

6. Arquitectura del agente

Se requiere entregar un diagrama de arquitectura que represente la solución propuesta e implementada.

Debe incluir como mínimo:

- Interacción con el usuario.

- Agente desarrollado con ADK.

- Identificación de intención.

- Selección y orquestación de herramientas.

- Extracción de argumentos.

- Validación de argumentos.

- Manejo de información faltante.

- Gestión de memoria y estado conversacional.

- Ejecución de herramientas.

- Manejo de resultados y errores.

- Feedback hacia el usuario.


El diagrama debe mostrar cómo interactúan los componentes y cómo se controla el ciclo de ejecución.

Se deberá explicar qué responsabilidades corresponden al LLM y cuáles son responsabilidad del código determinístico.

No se exige una arquitectura multiagente. Se permite implementar un único agente con múltiples herramientas o utilizar varios agentes si existe una justificación técnica.

La arquitectura presentada deberá corresponder con la implementación entregada.

7. Entregables en GitHub

El candidato deberá entregar un repositorio de GitHub que contenga:

1. Código fuente completo y funcional.

2. Implementación del agente utilizando Google ADK.
3. Definición e implementación de las herramientas.
4. Validación de argumentos.
5. Mecanismo de memoria seleccionado.
6. Manejo del estado conversacional.
7. Implementación de los casos de prueba.
8. Tests automatizados.
9. Diagrama de arquitectura.
10. Documentación técnica.
11. README con instrucciones para instalar, configurar y ejecutar el proyecto.
12. Ejemplos reproducibles de interacción con el agente.

El repositorio deberá permitir que otra persona pueda instalar y ejecutar la solución siguiendo las instrucciones documentadas.

No se deben incluir credenciales, API keys ni información sensible.

8. Evaluación técnica

La evaluación se realizará sobre 100 puntos.

Criterio| Puntuación
Identificación de intenciones| 10
Selección correcta de herramientas| 10
Extracción de argumentos| 10
Validación de argumentos| 15
Manejo de campos faltantes y feedback| 15
Memoria y contexto conversacional| 10
Orquestación de múltiples herramientas| 10
Seguridad, autorización e idempotencia| 10
Arquitectura, calidad de implementación y documentación| 10
Total| 100

9. Preguntas de defensa técnica

El candidato deberá explicar y justificar las siguientes decisiones:

1. ¿Cómo evita que el LLM invente argumentos faltantes?

2. ¿Cómo diferencia una intención informativa de una intención de ejecución?
3. ¿Cómo determina cuándo utilizar una o varias herramientas?
4. ¿Cómo mantiene el estado de una operación pendiente entre mensajes?
5. ¿Cómo resuelve contradicciones entre argumentos previamente recopilados?
6. ¿Cómo evita ejecutar dos veces una misma operación?
7. ¿Cómo maneja fallos parciales cuando se ejecutan varias herramientas?
8. ¿Qué responsabilidades corresponden al LLM y cuáles al código determinístico?
9. ¿Por qué seleccionó ese mecanismo de memoria?
10. ¿Cómo escalaría la solución para soportar decenas o cientos de herramientas?

10. Consideraciones de evaluación

Se valorará especialmente:

- Separación clara entre interpretación semántica y lógica de negocio.

- Control explícito del ciclo de ejecución.

- Uso correcto de Google ADK.

- Validación determinística de argumentos.

- Gestión consistente del estado conversacional.

- Capacidad para manejar múltiples herramientas y dependencias.

- Manejo de errores y recuperación ante fallos.

- Diseño extensible para incorporar nuevas herramientas.

- Calidad de la arquitectura y justificación de las decisiones técnicas.


No se evaluará positivamente una solución que dependa exclusivamente de un prompt extenso para controlar todas las decisiones del sistema.

Tampoco se requiere utilizar múltiples agentes por el simple hecho de utilizar ADK.

11. Resultado esperado

Al finalizar, se espera contar con un agente funcional capaz de recibir solicitudes en lenguaje natural, identificar las operaciones requeridas, solicitar información faltante, validar argumentos, ejecutar herramientas de forma controlada y comunicar sus resultados al usuario.

La solución debe demostrar fundamentos sólidos de ingeniería de software, diseño de sistemas y desarrollo de agentes basados en LLM.

El foco principal de la evaluación es la calidad de la orquestación y el control del ciclo de ejecución, no el modelo utilizado ni la complejidad del prompt.