import { useContext } from 'react';
import { ThemeContext } from '@/context/ThemeContextInstance';

export const useTheme = () => {
	const context = useContext(ThemeContext);
	if (!context) {
		throw new Error('useTheme must be used within a ThemeProvider');
	}
	return context;
};
