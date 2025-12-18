export type ThemeMode = 'light' | 'dark';
export type LayoutMode = 'side' | 'top';

export interface ThemeContextType {
	themeMode: ThemeMode;
	layoutMode: LayoutMode;
	primaryColor: string;
	fontSize: number;
	compactMode: boolean;
	borderRadius: number;
	setThemeMode: (mode: ThemeMode) => void;
	setLayoutMode: (mode: LayoutMode) => void;
	setPrimaryColor: (color: string) => void;
	setFontSize: (size: number) => void;
	setCompactMode: (mode: boolean) => void;
	setBorderRadius: (radius: number) => void;
}
