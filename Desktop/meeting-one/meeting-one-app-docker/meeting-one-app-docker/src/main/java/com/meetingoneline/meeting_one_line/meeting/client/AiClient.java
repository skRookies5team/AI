package com.meetingoneline.meeting_one_line.meeting.client;

import com.meetingoneline.meeting_one_line.global.exception.BusinessException;
import com.meetingoneline.meeting_one_line.global.exception.ErrorCode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Consumer;

@Slf4j
@Component
@RequiredArgsConstructor
public class AiClient {

    private final WebClient webClient;

    @Value("${ai.server.url}")
    private String aiServerUrl;


    /**
     * 유사성 검색 요청
     */
    public Mono<String> requestSearch(
            UUID userId,
            Integer page,
            Integer size,
            String keyword,
            String title,
            String summary,
            String status
    ) {
        String url = aiServerUrl + "/meetings";

        log.info("### AI 서버 회의록 목록 조회 요청: {}", url);

        return webClient.get()
                        .uri(uriBuilder -> uriBuilder
                                .path("/meetings") // 상대 경로만 사용해야 함 (전체 URL 쓰면 중복됨)
                                .queryParam("userId", userId)
                                .queryParam("page", page != null ? page : 1)
                                .queryParam("size", size != null ? size : 10)
                                .queryParamIfPresent("keyword", Optional.ofNullable(keyword))
                                .queryParamIfPresent("title", Optional.ofNullable(title))
                                .queryParamIfPresent("summary", Optional.ofNullable(summary))
                                .queryParamIfPresent("status", Optional.ofNullable(status))
                                .build())
                        .accept(MediaType.APPLICATION_JSON)
                        .retrieve()
                        .onStatus(
                                httpStatus -> httpStatus.is4xxClientError() || httpStatus.is5xxServerError(),
                                clientResponse -> clientResponse.bodyToMono(String.class)
                                                                .flatMap(body -> {
                                                                    log.error("### AI 서버 오류 응답 (/meetings): {}", body);
                                                                    return Mono.error(new RuntimeException("AI 서버 오류: " + body));
                                                                })
                        )
                        .bodyToMono(String.class)
                        .doOnNext(res -> log.info("### AI 서버 회의록 목록 조회 성공"))
                        .doOnError(err -> log.error("### AI 서버 회의록 목록 조회 실패: {}", err.getMessage()));
    }


    /**
     * AI 서버 분석 요청 (비동기 fire-and-forget)
     * @param onErrorFailHandler 실패 시 수행할 콜백 (예: DB 상태 변경)
     */
    public void requestAnalysis(UUID userId, UUID meetingId, String filePath, String meetingTitle, Consumer<Throwable> onErrorFailHandler) {
        Map<String, Object> requestBody = Map.of(
                "userId", userId.toString(),
                "meetingId", meetingId.toString(),
                "filePath", filePath,
                "meetingTitle", meetingTitle
        );

        log.info("### AI 서버에 분석 요청 시작: /ai/analyze {}", requestBody);

        webClient.post()
                 .uri("/ai/analyze")
                 .contentType(MediaType.APPLICATION_JSON)
                 .bodyValue(requestBody)
                 .retrieve()
                 .onStatus(
                         status -> status.is4xxClientError() || status.is5xxServerError(),
                         clientResponse -> clientResponse.bodyToMono(String.class)
                                                         .flatMap(body -> {
                                                             log.error("### AI 서버 오류 응답: {}", body);
                                                             return Mono.error(new RuntimeException("AI 서버 오류: " + body));
                                                         })
                 )
                 .bodyToMono(Void.class)
                 .doOnError(error -> {
                     log.error("### AI 서버 요청 실패 (meetingId={}): {}", meetingId, error.getMessage());
                     if (onErrorFailHandler != null) onErrorFailHandler.accept(error);
                 })
                 .doOnSuccess(v -> log.info("### AI 서버 요청 완료 (meetingId={})", meetingId))
                 .subscribe();
    }

    public void requestUpsertSync(UUID userId, UUID meetingId,
                              String title,
                              String summary,
                              List<String> keywords,
                              List<Map<String, Object>> speakers){
        Map<String, Object> requestBody = Map.of(
                "meetingId", meetingId,
                "title", title,
                "summary", summary,
                "keywords", keywords,
                "speakers", speakers,
                "userId", userId
        );

        log.info("### AI 서버 업서트 요청 시작 (/embeddings/upsert): {}", requestBody);

        webClient.post()
                 .uri("/embeddings/upsert")
                 .contentType(MediaType.APPLICATION_JSON)
                 .bodyValue(requestBody)
                 .retrieve()
                 .onStatus(
                         status -> status.is4xxClientError() || status.is5xxServerError(),
                         clientResponse -> clientResponse.bodyToMono(String.class)
                                                         .flatMap(body -> {
                                                             log.error("### ❌ AI 서버 오류 응답 (/embeddings/upsert): {}", body);
                                                             return Mono.error(new BusinessException(ErrorCode.AI_SERVER_ERROR, body));
                                                         })
                 )
                 .bodyToMono(Void.class)
                 .doOnError(error -> log.error("### AI 서버 업서트 요청 실패 (meetingId={}): {}", meetingId, error.getMessage()))
                 .doOnSuccess(v -> log.info("### AI 서버 업서트 요청 성공 (meetingId={})", meetingId))
                 .block();
    }

    /**
     * AI 서버 임베딩 삭제 요청
     * @param userId    사용자 ID (쿼리 파라미터)
     * @param meetingId 회의 ID (path variable)
     */
    public void requestDeleteEmbeddingSync(UUID userId, UUID meetingId) {
        log.info("### AI 서버 임베딩 삭제 요청 시작 (/embeddings/{}) userId={}", meetingId, userId);

        webClient.delete()
                 .uri(uriBuilder -> uriBuilder
                         .path("/embeddings/{meetingId}")
                         .queryParam("userId", userId.toString())
                         .build(meetingId.toString())
                 )
                 .retrieve()
                 .onStatus(
                         status -> status.is4xxClientError() || status.is5xxServerError(),
                         clientResponse -> clientResponse.bodyToMono(String.class)
                                                         .flatMap(body -> {
                                                             log.error("### ❌ AI 서버 삭제 오류 응답 (/embeddings/{{}}): {}", meetingId, body);
                                                             return Mono.error(new BusinessException(ErrorCode.AI_SERVER_ERROR, body));
                                                         })
                 )
                 .bodyToMono(Void.class)
                 .doOnSuccess(v -> log.info("### ✅ AI 서버 임베딩 삭제 성공 (meetingId={})", meetingId))
                 .block(); // ✅ 동기 호출
    }

}
