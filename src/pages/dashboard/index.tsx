import { Button, Input } from "antd"
import React from "react";

const Page: React.FC = () => {

    const onError = () => {
        throw new Error("测试错误");
    }

    // 这是一个会在渲染期间抛出错误的组件
    const Bomb = () => {
        throw new Error("渲染期间发生的错误 (React Error Boundary 测试)");
    }

    const [explode, setExplode] = React.useState(false);

    return (
        <>
            <Input />
            <h1>Dashboard</h1>
            <div style={{ display: 'flex', gap: '10px', marginTop: '20px' }}>
                <Button onClick={onError}>
                    普通JS报错 (不触发UI降级)
                </Button>
                <Button type="primary" danger onClick={() => {
                    throw new Error("Test Error");
                }}>
                    直接抛错 (不触发UI降级)
                </Button>
                <Button type="primary" danger onClick={() => setExplode(true)}>
                    触发渲染错误 (触发UI降级)
                </Button>
                <Button danger onClick={() => {
                    // Sentry 会自动捕获未处理的 Promise Rejection
                    setTimeout(() => {
                        Promise.reject(new Error("Promise reject 测试 (异步)"));
                    }, 1000)
                }}>
                    异步 Promise Reject
                </Button>
            </div>

            {explode && <Bomb />}
        </>
    )
}

export default Page