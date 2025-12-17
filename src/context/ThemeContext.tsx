import React, { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

export type ThemeMode = 'light' | 'dark';

export interface ThemeContextType {
	themeMode: ThemeMode;
	primaryColor: string;
	setThemeMode: (mode: ThemeMode) => void;
	setPrimaryColor: (color: string) => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export const ThemeProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
	const [themeMode, setThemeModeState] = useState<ThemeMode>(() => {
		const saved = localStorage.getItem('theme-mode');
		return (saved as ThemeMode) || 'light';
	});

	const [primaryColor, setPrimaryColorState] = useState<string>(() => {
		const saved = localStorage.getItem('primary-color');
		return saved || '#1677ff';
	});

	const setThemeMode = (mode: ThemeMode) => {
		setThemeModeState(mode);
		localStorage.setItem('theme-mode', mode);

		// Optional: Add class to body/html for Tailwind dark mode if needed
		if (mode === 'dark') {
			document.documentElement.classList.add('dark');
		} else {
			document.documentElement.classList.remove('dark');
		}
	};

	const setPrimaryColor = (color: string) => {
		setPrimaryColorState(color);
		localStorage.setItem('primary-color', color);
	};

	// Initialize class on mount
	useEffect(() => {
		if (themeMode === 'dark') {
			document.documentElement.classList.add('dark');
		} else {
			document.documentElement.classList.remove('dark');
		}
	}, [themeMode]);

	return (
		<ThemeContext.Provider value={{ themeMode, primaryColor, setThemeMode, setPrimaryColor }}>
			{children}
		</ThemeContext.Provider>
	);
};

export const useTheme = () => {
	const context = useContext(ThemeContext);
	if (!context) {
		throw new Error('useTheme must be used within a ThemeProvider');
	}
	return context;
};
