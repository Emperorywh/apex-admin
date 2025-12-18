import React, { useEffect, useState, type ReactNode } from 'react';
import type { ThemeMode, LayoutMode } from './types';
import { ThemeContext } from './ThemeContextInstance';

export const ThemeProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
	const [themeMode, setThemeModeState] = useState<ThemeMode>(() => {
		const saved = localStorage.getItem('theme-mode');
		return (saved as ThemeMode) || 'light';
	});

	const [layoutMode, setLayoutModeState] = useState<LayoutMode>(() => {
		const saved = localStorage.getItem('layout-mode');
		return (saved as LayoutMode) || 'side';
	});

	const [primaryColor, setPrimaryColorState] = useState<string>(() => {
		const saved = localStorage.getItem('primary-color');
		return saved || '#1677ff';
	});

	const [fontSize, setFontSizeState] = useState<number>(() => {
		const saved = localStorage.getItem('font-size');
		return saved ? parseInt(saved) : 14;
	});

	const [compactMode, setCompactModeState] = useState<boolean>(() => {
		const saved = localStorage.getItem('compact-mode');
		return saved === 'true';
	});

	const [borderRadius, setBorderRadiusState] = useState<number>(() => {
		const saved = localStorage.getItem('border-radius');
		return saved ? parseInt(saved) : 6;
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

	const setLayoutMode = (mode: LayoutMode) => {
		setLayoutModeState(mode);
		localStorage.setItem('layout-mode', mode);
	};

	const setPrimaryColor = (color: string) => {
		setPrimaryColorState(color);
		localStorage.setItem('primary-color', color);
	};

	const setFontSize = (size: number) => {
		setFontSizeState(size);
		localStorage.setItem('font-size', size.toString());
	};

	const setCompactMode = (mode: boolean) => {
		setCompactModeState(mode);
		localStorage.setItem('compact-mode', String(mode));
	};

	const setBorderRadius = (radius: number) => {
		setBorderRadiusState(radius);
		localStorage.setItem('border-radius', radius.toString());
	};

	useEffect(() => {
		if (themeMode === 'dark') {
			document.documentElement.classList.add('dark');
		} else {
			document.documentElement.classList.remove('dark');
		}
	}, [themeMode]);

	return (
		<ThemeContext.Provider value={{ themeMode, layoutMode, primaryColor, fontSize, compactMode, borderRadius, setThemeMode, setLayoutMode, setPrimaryColor, setFontSize, setCompactMode, setBorderRadius }}>
			{children}
		</ThemeContext.Provider>
	);
};
