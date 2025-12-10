package com.meetingoneline.meeting_one_line.auth;

import com.meetingoneline.meeting_one_line.auth.dto.AuthRequestDto;
import com.meetingoneline.meeting_one_line.auth.dto.AuthResponseDto;
import com.meetingoneline.meeting_one_line.auth.refresh_token.RefreshTokenEntity;
import com.meetingoneline.meeting_one_line.auth.refresh_token.RefreshTokenRepository;
import com.meetingoneline.meeting_one_line.global.config.jwt.JwtTokenProvider;
import com.meetingoneline.meeting_one_line.global.config.jwt.JwtType;
import com.meetingoneline.meeting_one_line.global.exception.BusinessException;
import com.meetingoneline.meeting_one_line.global.exception.ErrorCode;
import com.meetingoneline.meeting_one_line.user.UserEntity;
import com.meetingoneline.meeting_one_line.user.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseCookie;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class AuthService {
    private final UserRepository userRepository;
    private final RefreshTokenRepository refreshTokenRepository;
    private final JwtTokenProvider jwtTokenProvider;
    private final PasswordEncoder passwordEncoder;

    @Value("${jwt.refresh-expiration}")
    private long refreshExpirationMs;

    /**
     * 회원가입
     */
    @Transactional
    public AuthResponseDto.Token signup(AuthRequestDto.SignupRequest request){
        // 1. 이메일 중복 확인
        if(userRepository.findByEmail(request.getEmail()).isPresent()){
            throw new BusinessException(ErrorCode.DUPLICATE_EMAIL);
        }

        // 2. 닉네임 중복 확인
        if(userRepository.existsByNickname(request.getNickname())){
            throw new BusinessException(ErrorCode.DUPLICATE_NICKNAME);
        }

        // 3. 비빌번호 인코더
        String encoderPassword = passwordEncoder.encode(request.getPassword());

        // 4. 유저 생성
        UserEntity userEntity = UserEntity.create(request.getEmail(), encoderPassword, request.getNickname());

        // 5. 유저 정보 저장
        UserEntity savedUserEntity = userRepository.save(userEntity);

        String at = jwtTokenProvider.createToken(savedUserEntity.getId().toString(), JwtType.ACCESS);

        String rt = jwtTokenProvider.createToken(savedUserEntity.getId().toString(), JwtType.REFRESH);

        // 4. rt token 저장
        RefreshTokenEntity rtEntity = RefreshTokenEntity.create(savedUserEntity, rt);
        refreshTokenRepository.save(rtEntity);

        return AuthResponseDto.Token.builder()
                                     .accessToken(at)
                                     .refreshToken(rt)
                                     .build();
    }

    /**
     * 로그인
     */
    @Transactional
    public AuthResponseDto.Token login(AuthRequestDto.EmailLogin request){
        // 1. 이메일 존재 유무 체크
        UserEntity user = userRepository.findByEmail(request.getEmail())
                .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));

        // 2. 비밀번호 매치 체크
        if (!passwordEncoder.matches(request.getPassword(), user.getPassword())) {
            throw new BusinessException(ErrorCode.INVALID_LOGIN);
        }

        // 3. at, rt 생성
        String at = jwtTokenProvider.createToken(user.getId().toString(), JwtType.ACCESS);

        String rt = jwtTokenProvider.createToken(user.getId().toString(), JwtType.REFRESH);

        // 4. rt token 저장
        RefreshTokenEntity rtEntity = RefreshTokenEntity.create(user, rt);
        refreshTokenRepository.save(rtEntity);

        return AuthResponseDto.Token.builder()
                                    .accessToken(at)
                                    .refreshToken(rt)
                                    .build();
    }

    /**
     * Access Token 갱신
     */
    @Transactional
    public AuthResponseDto.Refresh refreshAccessToken(String refreshToken) {
        if (refreshToken == null) {
            throw new BusinessException(ErrorCode.ACCESS_DENIED, "Refresh Token이 쿠키에 존재하지 않습니다.");
        }

        // 1. 토큰 유효성 검증
        if (!jwtTokenProvider.validateToken(refreshToken)) {
            throw new BusinessException(ErrorCode.ACCESS_DENIED, "Refresh Token이 만료되었거나 유효하지 않습니다.");
        }

        // 2. 유저 조회
        String userId = jwtTokenProvider.getUserIdFromToken(refreshToken);
        UserEntity user = userRepository.findById(UUID.fromString(userId))
                                        .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));

        // 3. DB 존재 여부 확인
        boolean exists = refreshTokenRepository.existsByUserAndToken(user, refreshToken);
        if (!exists) {
            throw new BusinessException(ErrorCode.ACCESS_DENIED, "DB에 존재하지 않는 Refresh Token입니다.");
        }

        // 4. 새 Access Token 발급
        String newAccessToken = jwtTokenProvider.createToken(user.getId().toString(), JwtType.ACCESS);

        return AuthResponseDto.Refresh.builder()
                                      .accessToken(newAccessToken)
                                      .build();
    }


    /**
     * 닉네임 중복확인
     */
    public AuthResponseDto.NicknameDuplicate checkNickname(String nickname){
        boolean exists = userRepository.existsByNickname(nickname);

        return AuthResponseDto.NicknameDuplicate.builder().isDuplicate(exists)
                                                .build();
    }

    /**
     * 재석님 > 내정보 조회22
     */
    public AuthResponseDto.UserInfo getMyInfo(UUID userId) {
        UserEntity user = userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));

        return AuthResponseDto.UserInfo.builder()
                .id(user.getId())
                .email(user.getEmail())
                .nickname(user.getNickname())
                .build();
    }

    /**
     * 재석님 > 로그아웃
     */
    @Transactional
    public void logout(UUID userId) {
        UserEntity user = userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));
        refreshTokenRepository.deleteByUser(user);
    }

    /**
     * HttpOnly Refresh Token 쿠키 생성
     */
    public ResponseCookie createRefreshCookie(String refreshToken) {
        return ResponseCookie.from("refreshToken", refreshToken)
                             .httpOnly(true)
                             .secure(true)
                             .path("/")
                             .maxAge(refreshExpirationMs / 1000)
                             .sameSite("Strict")
                             .build();
    }
}
