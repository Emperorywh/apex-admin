import React, { useEffect } from 'react';

const Page: React.FC = () => {
    useEffect(() => {
        console.log("个人资料挂载");
        return () => {
            console.log("个人资料卸载");
        }
    }, [])
    return (
        <div>
            <h1>个人资料</h1>
        </div>
    );
};

export default Page;
