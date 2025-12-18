const TOKEN_KEY = 'ACCESS_TOKEN';
const REFRESH_TOKEN_KEY = 'REFRESH_TOKEN';

export function getToken() {
	return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
	return localStorage.setItem(TOKEN_KEY, token);
}

export function getRefreshToken() {
	return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setRefreshToken(token: string) {
	return localStorage.setItem(REFRESH_TOKEN_KEY, token);
}

export function clearAuth() {
	localStorage.removeItem(TOKEN_KEY);
	localStorage.removeItem(REFRESH_TOKEN_KEY);
}
