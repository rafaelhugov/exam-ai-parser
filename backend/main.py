import os
import json
import logging
import tempfile
from typing import List, Optional, Literal

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import google.generativeai as genai
from pdf2image import convert_from_bytes
import genanki

# ======================
# Configuração de Logs
# ======================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ======================
# App FastAPI + CORS
# ======================
app = FastAPI(title="ExamAI Parser API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # em produção, restringir
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# Configuração Gemini
# ======================
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
else:
    logger.warning("GOOGLE_API_KEY não encontrada no ambiente.")

# ======================
# Modelos Pydantic
# ======================

class Alternative(BaseModel):
    letra: str
    texto: str


class Question(BaseModel):
    """
    Modelo de questão padronizado para o ExamAI Parser.
    Suporta:
    - Itens C/E (tipo="CE")
    - Questões de 5 alternativas (tipo="MC5")
    """
    id: int
    tipo: str                    # "CE" ou "MC5"
    numero: str                  # número da questão/item
    disciplina: Optional[str] = None
    comando: Optional[str] = None
    enunciado: str
    alternativas: List[Alternative] = []
    gabarito: Optional[str] = None
    pagina: Optional[int] = None
    origem: Optional[str] = None

    # Campos usados pelo front/Anki (não preenchidos pela IA)
    code_snippet: Optional[str] = None
    answer: Optional[str] = None  # comentário/resolução para o verso do card

    class Config:
        extra = "ignore"  # ignora campos extras vindos do front/IA


class DeckRequest(BaseModel):
    deck_name: str
    questions: List[Question]


class CommentRequest(BaseModel):
    tipo: Literal["CE", "MC5"]
    enunciado: str
    alternativas: Optional[List[Alternative]] = None
    resposta_usuario: Optional[str] = None
    gabarito: Optional[str] = None


# ======================
# Prompts base
# ======================

PROMPT_PROVA = """
Você é um parser de provas de concurso da área de TI.

ENTRADA:
- Imagens de páginas de prova (FCC, Cebraspe, etc.).
- Algumas provas têm itens de CERTO/ERRADO (C/E).
- Outras provas têm questões de múltipla escolha com 5 alternativas (A, B, C, D, E).

IMPORTANTE:
- Você está recebendo APENAS UM LOTE (batch) de páginas da prova.
- Considere SOMENTE as questões que aparecem nessas páginas.
- Não tente inferir questões de outras páginas ou trechos.

TAREFA:
1. Leia TODAS as páginas recebidas neste lote.
2. Identifique cada questão ou item deste lote.
3. Para cada item, devolva um JSON com o seguinte formato:

{
  "questions": [
    {
      "id": <inteiro sequencial (pode começar em 1 neste lote)>,
      "tipo": "CE" ou "MC5",
      "numero": "<número da questão ou item>",
      "disciplina": "<texto ou vazio>",
      "comando": "<texto do comando que antecede o item, se houver>",
      "enunciado": "<texto da questão/item>",
      "alternativas": [
        { "letra": "A", "texto": "..." },
        { "letra": "B", "texto": "..." },
        { "letra": "C", "texto": "..." },
        { "letra": "D", "texto": "..." },
        { "letra": "E", "texto": "..." }
      ],
      "gabarito": null,
      "pagina": <número da página principal do item dentro deste lote (pode ser o índice relativo ou absoluto, se identificar)>,
      "origem": "<descrição da prova se reconhecida, senão vazio>"
    }
  ]
}

REGRAS:
- Para itens CEBRASPE de certo/errado, use "tipo": "CE" e deixe "alternativas": [].
- Para questões FCC de 5 alternativas, use "tipo": "MC5" e preencha as 5 alternativas (A–E).
- NÃO invente gabarito: deixe "gabarito": null.
- A saída DEVE ser um JSON ÚNICO, começando em "{" e terminando em "}", sem comentários, sem texto fora do JSON.
"""

PROMPT_GABARITO = """
Você receberá páginas de GABARITO de prova objetiva.

Você também está recebendo APENAS UM LOTE (batch) de páginas do gabarito.
Considere apenas as respostas que aparecem nessas páginas.

Extraia um JSON EXCLUSIVAMENTE no formato:

{
  "answers": [
    { "numero": "1", "gabarito": "C" },
    { "numero": "2", "gabarito": "E" },
    { "numero": "21", "gabarito": "B" }
  ]
}

REGRAS:
- Use "C" ou "E" para itens de certo/errado.
- Use "A", "B", "C", "D" ou "E" para múltipla escolha.
- Não invente números de questões que não existam no gabarito.
- Não retorne texto fora do JSON.
"""

# ======================
# Funções utilitárias
# ======================

# Removi o gemini-3-pro-preview para não bater mais em quota 0
MODEL_CANDIDATES = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-flash-latest",
]


def chunk_list(lst, size: int):
    """Divide uma lista em pedaços (batches) de tamanho 'size'."""
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def extract_text_from_response(response) -> str:
    """
    Em vez de usar response.text (que explode quando não há Part),
    monta manualmente o texto a partir de candidates/parts.
    """
    if not getattr(response, "candidates", None):
        return ""

    texts = []
    for c in response.candidates:
        if not getattr(c, "content", None):
            continue
        parts = getattr(c.content, "parts", []) or []
        for p in parts:
            if hasattr(p, "text") and p.text:
                texts.append(p.text)

    return "\n".join(texts).strip()


def call_gemini_json_with_fallback(prompt: str, images: List):
    """
    Chama a Gemini forçando resposta em JSON (response_mime_type="application/json"),
    iterando sobre uma lista de modelos candidatos até algum funcionar.

    Retorna: dict (resultado do json.loads sobre o texto extraído)
    """
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=500, detail="GOOGLE_API_KEY não configurada no ambiente.")

    last_exception = None

    for model_name in MODEL_CANDIDATES:
        try:
            logger.info(f"Tentando gerar com modelo: {model_name}")

            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config={
                    "temperature": 0.2,
                    "top_p": 0.9,
                    "top_k": 40,
                    "max_output_tokens": 4096,
                    "response_mime_type": "application/json",
                },
            )

            response = model.generate_content([prompt, *images])

            raw_text = extract_text_from_response(response)
            if not raw_text:
                logger.warning(
                    f"Modelo {model_name} retornou candidatos sem texto útil "
                    f"(pode ter sido abortado – finish_reason=2)."
                )
                last_exception = RuntimeError("Sem texto no response")
                continue

            logger.info("Resposta bruta da IA (primeiros 500 chars): %r", raw_text[:500])

            try:
                data = json.loads(raw_text)
                return data
            except json.JSONDecodeError as e:
                logger.error(f"Falha ao decodificar JSON para modelo {model_name}: {e}")
                last_exception = e
                continue

        except Exception as e:
            logger.warning(f"Falha ao usar {model_name}: {e}")
            last_exception = e
            continue

    logger.error("Todos os modelos falharam ao retornar JSON válido.")
    raise HTTPException(
        status_code=500,
        detail="Nenhum modelo Gemini retornou JSON válido ao processar a requisição."
    ) from last_exception


def processar_prova_e_gabarito(prova_images: List, gabarito_images: List) -> List[Question]:
    """
    1) Extrai questões da prova em BATCHES.
       -> Aqui vamos usar 1 página por batch pra reduzir tamanho da saída.
    2) Se houver gabarito, extrai respostas em BATCHES e associa às questões.
    """
    if not prova_images:
        raise HTTPException(status_code=400, detail="PDF da prova está vazio ou corrompido.")

    # Limite de páginas (ajustável)
    MAX_PAGES_PROVA = 20
    prova_sel = prova_images[:MAX_PAGES_PROVA]

    # ---- 1) Questões em batches de 1 página ----
    BATCH_SIZE_PROVA = 1
    batches_prova = list(chunk_list(prova_sel, BATCH_SIZE_PROVA))
    logger.info(f"Prova será processada em {len(batches_prova)} batch(es) de até {BATCH_SIZE_PROVA} página(s).")

    all_raw_questions = []

    for batch_index, batch in enumerate(batches_prova, start=1):
        logger.info(
            f"Processando batch de PROVA {batch_index}/{len(batches_prova)} "
            f"com {len(batch)} página(s)."
        )
        data_q_batch = call_gemini_json_with_fallback(PROMPT_PROVA, batch)

        # cada batch deve retornar um JSON com "questions": [...]
        questions_batch = data_q_batch.get("questions", [])
        if not isinstance(questions_batch, list):
            logger.warning(
                f"Lote {batch_index}: chave 'questions' ausente ou não é lista. JSON recebido: {data_q_batch}"
            )
            continue

        all_raw_questions.extend(questions_batch)

    if not all_raw_questions:
        raise HTTPException(
            status_code=500,
            detail="A IA não retornou nenhuma questão ao processar os batches da prova."
        )

    # Normaliza e reindexa globalmente
    normalized_questions: List[Question] = []
    for idx, q in enumerate(all_raw_questions, start=1):
        if "id" not in q:
            q["id"] = idx
        try:
            normalized_questions.append(Question(**q))
        except Exception as e:
            logger.warning(f"Falha ao normalizar questão {idx}: {e} | Dados: {q}")

    # ---- 2) Gabarito (se houver) ----
    if not gabarito_images:
        return normalized_questions

    MAX_PAGES_GAB = 8
    gab_sel = gabarito_images[:MAX_PAGES_GAB]

    BATCH_SIZE_GAB = 1
    batches_gab = list(chunk_list(gab_sel, BATCH_SIZE_GAB))
    logger.info(f"Gabarito será processado em {len(batches_gab)} batch(es) de até {BATCH_SIZE_GAB} página(s).")

    answers_map = {}

    for batch_index, batch in enumerate(batches_gab, start=1):
        logger.info(
            f"Processando batch de GABARITO {batch_index}/{len(batches_gab)} "
            f"com {len(batch)} página(s)."
        )
        data_g_batch = call_gemini_json_with_fallback(PROMPT_GABARITO, batch)

        answers = data_g_batch.get("answers", [])
        if not isinstance(answers, list):
            logger.warning(
                f"Lote de gabarito {batch_index}: chave 'answers' ausente ou não é lista. JSON: {data_g_batch}"
            )
            continue

        for a in answers:
            num = a.get("numero")
            gab = a.get("gabarito")
            if num and gab:
                answers_map[str(num).strip()] = str(gab).strip().upper()

    # junta gabarito com questões
    for q in normalized_questions:
        num = str(q.numero).strip()
        if num in answers_map:
            q.gabarito = answers_map[num]

    return normalized_questions


# ======================
# Endpoints
# ======================

@app.post("/upload")
async def upload_pdf(
    prova: UploadFile = File(...),
    gabarito: UploadFile = File(None)
):
    """
    Recebe:
    - prova: PDF da prova
    - gabarito: PDF do gabarito (opcional)

    Retorna:
    - {"questions": [ Question, ... ]}
    """
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=500, detail="GOOGLE_API_KEY não configurada no ambiente.")

    try:
        logger.info("Recebendo arquivo de PROVA...")
        prova_bytes = await prova.read()
        prova_images = convert_from_bytes(prova_bytes)

        gabarito_images: List = []
        if gabarito is not None:
            logger.info("Recebendo arquivo de GABARITO...")
            gab_bytes = await gabarito.read()
            gabarito_images = convert_from_bytes(gab_bytes)

        questions = processar_prova_e_gabarito(prova_images, gabarito_images)

        # devolve como dicts para o front
        return {"questions": [q.dict() for q in questions]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro fatal no endpoint /upload: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/comment")
async def comment(req: CommentRequest):
    """
    Gera comentário/explicação da IA para uma questão específica.
    """
    if not GOOGLE_API_KEY:
        raise HTTPException(status_code=500, detail="GOOGLE_API_KEY não configurada no ambiente.")

    try:
        alternativas_txt = ""
        if req.tipo == "MC5" and req.alternativas:
            linhas = [f"{a.letra}) {a.texto}" for a in req.alternativas]
            alternativas_txt = "ALTERNATIVAS:\n" + "\n".join(linhas)

        prompt = f"""
Você é um professor de concursos de TI.

ENUNCIADO:
{req.enunciado}

{alternativas_txt}

Tipo de questão: {req.tipo}

Gabarito oficial: {req.gabarito or "desconhecido"}
Resposta do aluno: {req.resposta_usuario or "não respondida"}

Explique se a resposta do aluno está correta ou não (se existir),
e faça um comentário técnico, objetivo e curto, focado em concurso.
"""
        model = genai.GenerativeModel(
            "gemini-2.5-flash",
            generation_config={
                "temperature": 0.2,
                "top_p": 0.9,
                "top_k": 40,
                "max_output_tokens": 2048,
            },
        )
        resp = model.generate_content(prompt)
        text = extract_text_from_response(resp)
        comment_text = (text or "").strip()
        return {"comment": comment_text}

    except Exception as e:
        logger.error(f"Erro ao gerar comentário da IA: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-anki")
async def generate_anki(request: DeckRequest):
    """
    Gera um deck .apkg do Anki com base nas questões e comentários.
    """
    try:
        deck = genanki.Deck(
            2059400110,
            request.deck_name
        )

        model = genanki.Model(
            1607392319,
            'ExamAI Parser Model',
            fields=[
                {'name': 'Front'},
                {'name': 'Back'},
            ],
            templates=[
                {
                    'name': 'Card 1',
                    'qfmt': '{{Front}}',
                    'afmt': '{{Front}}<hr id="answer">{{Back}}',
                },
            ],
            css="""
.card {
  font-family: arial;
  font-size: 18px;
  text-align: left;
  color: black;
  background-color: white;
}
pre {
  background: #f4f4f4;
  padding: 10px;
}
"""
        )

        for q in request.questions:
            # Frente do card
            front_parts = []

            titulo = f"Questão {q.numero} ({q.tipo})"
            if q.disciplina:
                titulo += f" - {q.disciplina}"
            front_parts.append(f"<h3>{titulo}</h3>")

            if q.comando:
                front_parts.append(f"<p><em>{q.comando}</em></p>")

            front_parts.append(f"<div style='margin-bottom:10px'>{q.enunciado}</div>")

            if q.alternativas:
                front_parts.append("<ul>")
                for alt in q.alternativas:
                    front_parts.append(f"<li><b>{alt.letra})</b> {alt.texto}</li>")
                front_parts.append("</ul>")

            if q.code_snippet:
                code = q.code_snippet.replace("<", "&lt;").replace(">", "&gt;")
                front_parts.append(f"<pre><code>{code}</code></pre>")

            front_text = "\n".join(front_parts)

            # Verso do card
            back_parts = []
            if q.gabarito:
                back_parts.append(f"<p><b>Gabarito:</b> {q.gabarito}</p>")

            if q.answer:
                back_parts.append(f"<div>{q.answer}</div>")
            else:
                back_parts.append("<p><em>Resposta/comentário não fornecido.</em></p>")

            back_text = "\n".join(back_parts)

            note = genanki.Note(model=model, fields=[front_text, back_text])
            deck.add_note(note)

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.apkg')
        genanki.Package(deck).write_to_file(tmp.name)

        return FileResponse(
            tmp.name,
            filename=f"{request.deck_name.replace(' ', '_')}.apkg",
            media_type='application/octet-stream'
        )

    except Exception as e:
        logger.error(f"Erro ao gerar Anki: {e}")
        raise HTTPException(status_code=500, detail=str(e))
