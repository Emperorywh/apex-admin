import type React from "react";
import { useState, type ReactNode } from "react";
import { useLocation, useOutlet } from "react-router";


const KeepAlive: React.FC = () => {

    const [cachedNodes, setCachedNodes] = useState<Map<string, ReactNode>>(new Map());

    const currentElement = useOutlet();

    const location = useLocation();

    const uniqueId = `${location.pathname}_${location?.state?.id}`;

    if (!cachedNodes.has(uniqueId)) {
        setCachedNodes(prev => {
            const newMap = new Map(prev);
            newMap.set(uniqueId, currentElement);
            return newMap;
        })
    }

    return Array.from(cachedNodes.entries()).map(([id, element]) => (
        <div
            key={id}
            style={{ display: id === uniqueId ? 'block' : 'none' }}
        >
            {element}
        </div>
    ))
}

export default KeepAlive;