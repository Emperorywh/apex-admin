import { Button } from "antd"

const Home = () => {

    console.log("Home");

    const onError = () => {
        throw new Error("测试错误");
    }

    return (
        <>
            <h1>Home</h1>
            <Button onClick={onError}>
                错误上报测试
            </Button>
            <Button type="primary" danger onClick={() => {
                
            }}>报错啦</Button>
        </>
    )
}

export default Home