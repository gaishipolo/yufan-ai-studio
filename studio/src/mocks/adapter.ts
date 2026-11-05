import { AxiosError, type AxiosAdapter, type AxiosResponse } from 'axios';
import './routes';
import {
  delay,
  jsonHeaders,
  ok,
  pathOf,
  randomLatency,
  readBody,
  readHeaders,
  type HttpError,
  type MockContext,
  type MockMethod,
} from './kernel';
import { mockRouteTable, resolveMockRoute } from './registry';

/* ------------------------------------------------------------------
 * Mock 适配器：在 axios 适配器层拦截请求，模拟真实后端
 *
 * 这里只做「分发」：路由表来自 src/mocks/routes/*.mock.ts 与
 * src/modules/<id>/mock.ts 的自动注册，因此新增接口不用改本文件。
 * 后端就绪后把 VITE_USE_MOCK 置为 false 即可整体停用。
 * ------------------------------------------------------------------ */

export const mockAdapter: AxiosAdapter = async (config) => {
  await delay(randomLatency());

  const method = (config.method ?? 'get').toUpperCase() as MockMethod;
  const path = pathOf(config.url ?? '');
  const route = resolveMockRoute(method, path);

  if (!route) {
    const registered = mockRouteTable()
      .map((item) => `${item.method} ${item.pattern}`)
      .join(' | ');
    return Promise.reject(
      new AxiosError(
        `[mock] 未实现的接口：${method} ${path}。已注册：${registered || '无'}`,
        'ERR_BAD_REQUEST',
        config,
        null,
        undefined,
      ),
    );
  }

  const ctx: MockContext = {
    method,
    path,
    params: (config.params ?? {}) as Record<string, unknown>,
    body: readBody(config),
    headers: readHeaders(config),
  };

  try {
    const payload = route.handler(ctx);
    const response: AxiosResponse = {
      data: ok(payload),
      status: route.status ?? 200,
      statusText: 'OK',
      headers: jsonHeaders(),
      config,
    };
    return response;
  } catch (error) {
    const err = error as HttpError;
    const status = err.status ?? 500;
    return Promise.reject(
      new AxiosError(
        err.message || '服务器异常',
        status === 404 ? 'ERR_NOT_FOUND' : 'ERR_BAD_REQUEST',
        config,
        null,
        {
          data: { code: status, message: err.message, data: null },
          status,
          statusText: 'ERROR',
          headers: jsonHeaders(),
          config,
        },
      ),
    );
  }
};
