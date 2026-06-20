import axios from 'axios'

const TOKEN_KEY = 'nutrifit_access_token'

export const api = axios.create({
  baseURL: '',
  timeout: 60000
})

api.interceptors.request.use(config => {
  const token = getAuthToken()
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

export function getAuthToken() {
  return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY)
}

export function setAuthToken(token: string, remember = true) {
  clearAuthToken()
  const storage = remember ? localStorage : sessionStorage
  storage.setItem(TOKEN_KEY, token)
}

export function clearAuthToken() {
  localStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(TOKEN_KEY)
}

export async function login(username: string, password: string, remember = true) {
  const data = (await api.post('/api/auth/login', { username, password })).data
  setAuthToken(data.access_token, remember)
  return data
}

export async function getMe() {
  return (await api.get('/api/auth/me')).data
}

export async function getSummary(userId?: string) {
  return (await api.get('/api/dashboard/summary', { params: userId ? { user_id: userId } : undefined })).data
}

export async function calcProfile(payload: any) {
  return (await api.post('/api/profile/calculate', payload)).data
}

export async function chat(question: string, topK = 6) {
  return (await api.post('/api/rag/chat', { question, top_k: topK })).data
}

export async function agentChat(payload: any) {
  return (await api.post('/api/agent/chat', payload)).data
}

export async function agentChatStream(payload: any, onEvent: (event: any) => void) {
  const token = getAuthToken()
  const response = await fetch('/api/agent/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    },
    body: JSON.stringify(payload)
  })
  if (!response.ok || !response.body) {
    throw new Error(`Agent stream failed: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.trim()) continue
      onEvent(JSON.parse(line))
    }
  }

  buffer += decoder.decode()
  if (buffer.trim()) {
    onEvent(JSON.parse(buffer))
  }
}

export async function getChatHistory(sessionId: string) {
  return (await api.get(`/api/chat/history/${sessionId}`)).data
}

export async function retrieve(question: string, topK = 6) {
  return (await api.post('/api/rag/retrieve', { question, top_k: topK })).data
}

export async function logFood(items: any[], note = '') {
  return (await api.post('/api/food/log', { items, note })).data
}

export async function getFoodRecords() {
  return (await api.get('/api/food/records')).data
}

export async function recognizeFood(file: File, mealType = '拍照记录') {
  const form = new FormData()
  form.append('file', file)
  form.append('meal_type', mealType)
  return (await api.post('/api/food/recognize', form, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })).data
}

export async function knowledgeStats() {
  return (await api.get('/api/knowledge/stats')).data
}

export async function saveBodyMetric(payload: any) {
  return (await api.post('/api/memory/body-metrics', payload)).data
}

export async function getBodyMetrics(userId?: string) {
  return (await api.get('/api/memory/body-metrics', { params: userId ? { user_id: userId } : undefined })).data
}

export async function getPublicConfig() {
  return (await api.get('/api/config/public')).data
}

export async function clearFoodRecords() {
  return (await api.post('/api/admin/clear-food-records')).data
}

export async function clearAllData() {
  return (await api.post('/api/admin/clear-all-data')).data
}
