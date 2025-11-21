<script setup>
import { ref } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import 'highlight.js/styles/atom-one-dark.css'

// Configuração do Marked com Highlight.js (para uso futuro, se quiser)
marked.setOptions({
  highlight(code, lang) {
    const language = hljs.getLanguage(lang) ? lang : 'plaintext'
    return hljs.highlight(code, { language }).value
  },
  langPrefix: 'hljs language-'
})

// ---------- ESTADO ----------

const fileProva = ref(null)
const fileGabarito = ref(null)

const questions = ref([])
const loading = ref(false)
const errorMessage = ref('')
const deckName = ref('Concurso TI')
const progressMessage = ref('')   // feedback de processo

const API_BASE = 'http://localhost:8000'

// ---------- FUNÇÕES ----------

const onFileProvaChange = (e) => {
  fileProva.value = e.target.files[0] || null
}

const onFileGabaritoChange = (e) => {
  fileGabarito.value = e.target.files[0] || null
}

const processPdf = async () => {
  if (!fileProva.value) {
    alert('Selecione o PDF da prova')
    return
  }

  loading.value = true
  errorMessage.value = ''
  progressMessage.value = 'Lendo PDF e enviando para a IA...'
  questions.value = []

  const formData = new FormData()
  formData.append('prova', fileProva.value)
  if (fileGabarito.value) {
    formData.append('gabarito', fileGabarito.value)
  }

  try {
    const res = await fetch(`${API_BASE}/upload`, {
      method: 'POST',
      body: formData
    })

    progressMessage.value = 'A IA está analisando as páginas da prova...'

    let data = {}
    let rawText = ''

    // Lê sempre como texto e *depois* tenta fazer parse como JSON
    try {
      rawText = await res.text()
      data = rawText ? JSON.parse(rawText) : {}
    } catch (e) {
      console.warn('Resposta não é JSON puro. Corpo bruto:', rawText)
      data = {}
    }

    if (!res.ok) {
      const msgBackend = data && data.detail ? data.detail : null
      const msg = msgBackend || `Erro ${res.status} do servidor.`
      throw new Error(msg)
    }

    progressMessage.value = 'Montando lista de questões extraídas...'

    if (data.questions && Array.isArray(data.questions)) {
      questions.value = data.questions.map((q) => ({
        ...q,
        respostaUsuario: null,
        comentarioIA: '',
        loadingIA: false,
        answer: q.answer || '' // verso do card Anki
      }))
      progressMessage.value = ''
    } else {
      throw new Error('A IA não retornou as questões no formato esperado.')
    }
  } catch (e) {
    console.error(e)
    errorMessage.value = `Erro: ${e.message}`
    progressMessage.value = ''
  } finally {
    loading.value = false
  }
}

const selecionarResposta = (q, letra) => {
  q.respostaUsuario = letra
}

const gerarComentarioIA = async (q) => {
  if (q.loadingIA) return

  q.loadingIA = true
  try {
    const body = {
      tipo: q.tipo,
      enunciado: q.enunciado,
      alternativas: q.alternativas || [],
      resposta_usuario: q.respostaUsuario,
      gabarito: q.gabarito
    }

    const res = await fetch(`${API_BASE}/comment`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })

    let data = {}
    let rawText = ''

    try {
      rawText = await res.text()
      data = rawText ? JSON.parse(rawText) : {}
    } catch (e) {
      console.warn('Resposta não é JSON puro em /comment. Corpo bruto:', rawText)
      data = {}
    }

    if (!res.ok) {
      const msgBackend = data && data.detail ? data.detail : null
      const msg = msgBackend || `Erro ${res.status} ao gerar comentário da IA.`
      throw new Error(msg)
    }

    q.comentarioIA = data.comment || ''
    if (!q.answer) {
      q.answer = q.comentarioIA
    }
  } catch (e) {
    alert(`Erro ao gerar comentário: ${e.message}`)
  } finally {
    q.loadingIA = false
  }
}

const generateAnki = async () => {
  if (questions.value.length === 0) return

  try {
    const res = await fetch(`${API_BASE}/generate-anki`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        deck_name: deckName.value,
        questions: questions.value
      })
    })

    if (!res.ok) {
      let data = {}
      let rawText = ''
      try {
        rawText = await res.text()
        data = rawText ? JSON.parse(rawText) : {}
      } catch (e) {
        data = {}
      }
      const msgBackend = data && data.detail ? data.detail : null
      const msg = msgBackend || `Erro ${res.status} ao gerar deck Anki.`
      throw new Error(msg)
    }

    const blob = await res.blob()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${deckName.value}.apkg`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
  } catch (e) {
    alert('Erro ao gerar deck: ' + e.message)
  }
}

// se quiser usar markdown depois
const renderMarkdown = (text) => {
  return marked(text || '')
}
</script>

<template>
  <div class="min-h-screen bg-gray-900 text-gray-100 font-mono p-8">
    <header class="mb-4 border-b border-green-500 pb-4">
      <h1 class="text-3xl font-bold text-green-400">
        ExamAI Parser <span class="text-sm text-gray-500 align-middle">v0.2</span>
      </h1>
      <p class="text-sm text-gray-400 mt-1">
        PDF to Anki com Visão Computacional – suporta C/E (Cebraspe) e múltipla escolha (FCC).
      </p>
    </header>

    <!-- BARRA DE STATUS / LOADING -->
    <div v-if="loading || progressMessage" class="mb-6">
      <div
        class="flex items-center gap-3 bg-gray-800 border border-green-500/70 text-green-200
               text-xs px-4 py-2 rounded shadow-md"
      >
        <!-- spinner simples -->
        <svg
          class="animate-spin h-4 w-4 text-green-400"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle
            class="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            stroke-width="4"
          ></circle>
          <path
            class="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
          ></path>
        </svg>
        <div>
          <p class="font-bold">Processando com IA...</p>
          <p class="text-[11px] text-green-300">
            {{ progressMessage || 'Isso pode levar alguns segundos dependendo do tamanho do PDF.' }}
          </p>
        </div>
      </div>
    </div>

    <!-- Upload -->
    <section class="mb-8">
      <div class="bg-gray-800 border border-gray-700 rounded-lg p-6 shadow-lg">
        <h2 class="text-lg font-semibold mb-4 text-green-300">Upload da Prova (PDF)</h2>
        <div class="grid gap-4 md:grid-cols-2">
          <div>
            <label class="block text-xs text-gray-400 mb-1">Prova (obrigatório)</label>
            <input
              type="file"
              accept="application/pdf"
              @change="onFileProvaChange"
              class="block w-full text-sm text-gray-300
                     file:mr-4 file:py-2 file:px-4
                     file:rounded file:border-0
                     file:text-sm file:font-semibold
                     file:bg-green-900 file:text-green-400
                     hover:file:bg-green-800"
            />
          </div>

          <div>
            <label class="block text-xs text-gray-400 mb-1">Gabarito (opcional)</label>
            <input
              type="file"
              accept="application/pdf"
              @change="onFileGabaritoChange"
              class="block w-full text-sm text-gray-300
                     file:mr-4 file:py-2 file:px-4
                     file:rounded file:border-0
                     file:text-sm file:font-semibold
                     file:bg-blue-900 file:text-blue-400
                     hover:file:bg-blue-800"
            />
          </div>
        </div>

        <div class="mt-6 flex justify-end">
          <button
            @click="processPdf"
            :disabled="loading"
            class="px-6 py-2 bg-green-600 hover:bg-green-500 text-white font-bold rounded
                   disabled:opacity-50 transition-colors"
          >
            {{ loading ? 'ANALISANDO IA...' : 'PROCESSAR ARQUIVOS' }}
          </button>
        </div>

        <!-- MENSAGEM DE ERRO -->
        <div
          v-if="errorMessage"
          class="mt-4 p-3 bg-red-900/50 border border-red-500 text-red-200 rounded text-sm font-bold"
        >
          🚨 {{ errorMessage }}
        </div>
      </div>
    </section>

    <!-- Lista de Questões -->
    <section v-if="questions && questions.length > 0">
      <div class="flex flex-col md:flex-row md:items-center md:justify-between mb-4 gap-3">
        <h2 class="text-xl font-bold text-green-400">
          Questões Extraídas ({{ questions.length }})
        </h2>
        <div class="flex gap-2 items-center">
          <input
            v-model="deckName"
            class="bg-gray-800 border border-gray-600 focus:border-green-500 outline-none text-white
                   rounded px-3 py-1 text-sm"
            placeholder="Nome do Deck"
          />
          <button
            @click="generateAnki"
            class="bg-blue-600 hover:bg-blue-500 px-4 py-1 rounded text-sm font-bold transition-colors text-white"
          >
            DOWNLOAD .APKG
          </button>
        </div>
      </div>

      <div class="grid gap-6">
        <div
          v-for="q in questions"
          :key="q.id"
          class="bg-gray-800 border border-gray-700 rounded-lg p-4 shadow-lg hover:border-gray-500 transition-colors"
        >
          <div class="flex justify-between mb-2">
            <span class="text-xs bg-gray-700 px-2 py-1 rounded text-green-200 font-bold">
              {{ q.tipo }}
            </span>
            <span class="text-xs text-gray-500">
              ID: {{ q.id }}
              <span v-if="q.numero">• Q{{ q.numero }}</span>
              <span v-if="q.pagina"> • p. {{ q.pagina }}</span>
            </span>
          </div>

          <p v-if="q.origem" class="text-xs text-gray-400 mb-1">
            {{ q.origem }}
          </p>

          <p v-if="q.comando" class="text-xs text-blue-300 mb-2 italic">
            {{ q.comando }}
          </p>

          <div class="mb-3 text-sm whitespace-pre-line">
            {{ q.enunciado }}
          </div>

          <!-- Alternativas -->
          <ul
            v-if="q.alternativas && q.alternativas.length"
            class="space-y-1 mb-3 text-sm"
          >
            <li
              v-for="alt in q.alternativas"
              :key="alt.letra"
              class="flex items-start gap-2 text-gray-300"
            >
              <span class="text-green-400 font-bold mt-0.5">{{ alt.letra }})</span>
              <span>{{ alt.texto }}</span>
            </li>
          </ul>

          <!-- Responder -->
          <div class="mb-3">
            <label class="block text-xs text-gray-400 mb-1">Responder</label>

            <!-- C/E -->
            <div v-if="q.tipo === 'CE'" class="flex gap-2">
              <button
                class="px-3 py-1 rounded text-xs font-bold border border-gray-600"
                :class="q.respostaUsuario === 'C' ? 'bg-green-600 text-white border-green-400' : 'bg-gray-700 text-gray-200'"
                @click="selecionarResposta(q, 'C')"
              >
                CERTO
              </button>
              <button
                class="px-3 py-1 rounded text-xs font-bold border border-gray-600"
                :class="q.respostaUsuario === 'E' ? 'bg-red-600 text-white border-red-400' : 'bg-gray-700 text-gray-200'"
                @click="selecionarResposta(q, 'E')"
              >
                ERRADO
              </button>
            </div>

            <!-- Múltipla escolha -->
            <div v-else-if="q.tipo === 'MC5'" class="flex flex-wrap gap-2">
              <button
                v-for="alt in q.alternativas"
                :key="alt.letra"
                class="px-3 py-1 rounded text-xs font-bold border border-gray-600"
                :class="q.respostaUsuario === alt.letra
                  ? 'bg-green-600 text-white border-green-400'
                  : 'bg-gray-700 text-gray-200'"
                @click="selecionarResposta(q, alt.letra)"
              >
                {{ alt.letra }})
              </button>
            </div>

            <p
              v-if="q.gabarito && q.respostaUsuario"
              class="mt-2 text-xs"
              :class="q.respostaUsuario === q.gabarito ? 'text-green-400' : 'text-red-400'"
            >
              Sua resposta: {{ q.respostaUsuario }} • Gabarito: {{ q.gabarito }}
            </p>
          </div>

          <!-- Comentário da IA -->
          <div class="mb-3">
            <button
              class="px-3 py-1 rounded text-xs font-bold border border-blue-500 text-blue-300
                     hover:bg-blue-600 hover:text-white transition-colors disabled:opacity-50"
              :disabled="q.loadingIA"
              @click="gerarComentarioIA(q)"
            >
              {{ q.loadingIA ? 'Gerando comentário...' : 'Comentário da IA' }}
            </button>
          </div>

          <div v-if="q.comentarioIA" class="mb-3 text-xs text-gray-300 bg-gray-900 border border-gray-700 rounded p-2">
            <span class="font-bold text-blue-300">Comentário IA:</span>
            <div class="mt-1 whitespace-pre-line">
              {{ q.comentarioIA }}
            </div>
          </div>

          <!-- Campo de Resposta / Verso do card -->
          <div>
            <label class="text-xs text-blue-300 uppercase font-bold mb-1 block">
              Gabarito / Comentário (para Anki)
            </label>
            <textarea
              v-model="q.answer"
              class="w-full bg-gray-900 border border-gray-600 rounded p-2 text-sm
                     focus:border-blue-500 focus:outline-none transition-colors text-gray-300"
              rows="3"
              placeholder="Digite sua resolução ou comentário para aparecer no verso do card..."
            ></textarea>
          </div>
        </div>
      </div>
    </section>

    <!-- Estado Vazio -->
    <section
      v-else-if="!loading && !errorMessage"
      class="text-center text-gray-600 mt-12"
    >
      <p>Nenhuma questão processada ainda.</p>
    </section>
  </div>
</template>

<style>
pre {
  margin: 0;
}
</style>
