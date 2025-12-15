import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import enUS from "@/locales/en-US/translation.json";
import zhCN from "@/locales/zh-CN/translation.json";

const resources = {
    'en-US': { translation: enUS },
    'zh-CN': { translation: zhCN },
} as const;

i18n
    .use(initReactI18next) // passes i18n down to react-i18next
    .init({
        resources,
        lng: "zh-CN", 
        interpolation: {
            escapeValue: false // react already safes from xss
        }
    });

export default i18n;