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
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;
import org.springframework.security.crypto.password.PasswordEncoder;

import java.lang.reflect.Field;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class AuthServiceTest {

    @Mock
    private UserRepository userRepository;

    @Mock
    private RefreshTokenRepository refreshTokenRepository;

    @Mock
    private JwtTokenProvider jwtTokenProvider;

    @Mock
    private PasswordEncoder passwordEncoder;

    @InjectMocks
    private AuthService authService;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
    }

    /**
     * 회원가입 테스트
     */
    @Nested
    @DisplayName("회원가입 로직 테스트")
    class Signup {

        @Test
        @DisplayName("회원가입 성공 시 AT, RF Token 반환")
        void signup_success() throws Exception {
            // given
            AuthRequestDto.SignupRequest request =
                    new AuthRequestDto.SignupRequest("new@test.com", "password123", "cookie");

            when(userRepository.findByEmail(request.getEmail())).thenReturn(Optional.empty());
            when(userRepository.existsByNickname(request.getNickname())).thenReturn(false);
            when(passwordEncoder.encode(request.getPassword())).thenReturn("encodedPw");

            UserEntity savedUser = UserEntity.create("new@test.com", "encodedPw", "cookie");

            // 리플렉션으로 id 세팅
            Field idField = UserEntity.class.getSuperclass().getSuperclass().getDeclaredField("id");
            idField.setAccessible(true);
            idField.set(savedUser, UUID.randomUUID());

            when(userRepository.save(any(UserEntity.class))).thenReturn(savedUser);
            when(jwtTokenProvider.createToken(anyString(), eq(JwtType.ACCESS))).thenReturn("access-token");
            when(jwtTokenProvider.createToken(anyString(), eq(JwtType.REFRESH))).thenReturn("refresh-token");

            // when
            AuthResponseDto.Token result = authService.signup(request);

            // then
            assertThat(result).isNotNull();
            assertThat(result.getAccessToken()).isEqualTo("access-token");
            assertThat(result.getRefreshToken()).isEqualTo("refresh-token");

            verify(userRepository).save(any(UserEntity.class));
            verify(refreshTokenRepository).save(any(RefreshTokenEntity.class));
        }

        @Test
        @DisplayName("중복된 이메일이면 DUPLICATE_EMAIL 예외 발생")
        void signup_duplicateEmail() {
            // given
            AuthRequestDto.SignupRequest request =
                    new AuthRequestDto.SignupRequest("dup@test.com", "password", "nickname");

            when(userRepository.findByEmail(request.getEmail()))
                    .thenReturn(Optional.of(mock(UserEntity.class)));

            // when & then
            assertThatThrownBy(() -> authService.signup(request))
                    .isInstanceOf(BusinessException.class)
                    .hasMessageContaining(ErrorCode.DUPLICATE_EMAIL.getMessage());
        }

        @Test
        @DisplayName("중복된 닉네임이면 DUPLICATE_NICKNAME 예외 발생")
        void signup_duplicateNickname() {
            // given
            AuthRequestDto.SignupRequest request =
                    new AuthRequestDto.SignupRequest("test@test.com", "password", "dupname");

            when(userRepository.findByEmail(request.getEmail())).thenReturn(Optional.empty());
            when(userRepository.existsByNickname(request.getNickname())).thenReturn(true);

            // when & then
            assertThatThrownBy(() -> authService.signup(request))
                    .isInstanceOf(BusinessException.class)
                    .hasMessageContaining(ErrorCode.DUPLICATE_NICKNAME.getMessage());
        }
    }

    /**
     * 로그인 테스트
     */
    @Nested
    @DisplayName("로그인 로직 테스트")
    class Login {

        @Test
        @DisplayName("로그인 성공 시 AccessToken과 RefreshToken을 반환")
        void login_success() throws Exception {
            // given
            AuthRequestDto.EmailLogin request =
                    new AuthRequestDto.EmailLogin("test@test.com", "12345678");

            UserEntity mockUser = UserEntity.create("test@test.com", "encodedPw", "tester");

            Field idField = UserEntity.class.getSuperclass().getSuperclass().getDeclaredField("id");
            idField.setAccessible(true);
            idField.set(mockUser, UUID.randomUUID());

            when(userRepository.findByEmail(request.getEmail())).thenReturn(Optional.of(mockUser));
            when(passwordEncoder.matches(request.getPassword(), mockUser.getPassword())).thenReturn(true);
            when(jwtTokenProvider.createToken(anyString(), eq(JwtType.ACCESS))).thenReturn("access-token");
            when(jwtTokenProvider.createToken(anyString(), eq(JwtType.REFRESH))).thenReturn("refresh-token");

            // when
            AuthResponseDto.Token result = authService.login(request);

            // then
            assertThat(result).isNotNull();
            assertThat(result.getAccessToken()).isEqualTo("access-token");
            assertThat(result.getRefreshToken()).isEqualTo("refresh-token");
            verify(refreshTokenRepository, times(1)).save(any(RefreshTokenEntity.class));
        }

        @Test
        @DisplayName("존재하지 않는 이메일이면 USER_NOT_FOUND 예외 발생")
        void login_userNotFound() {
            // given
            AuthRequestDto.EmailLogin request =
                    new AuthRequestDto.EmailLogin("no@no.com", "12345678");

            when(userRepository.findByEmail(request.getEmail())).thenReturn(Optional.empty());

            // when & then
            assertThatThrownBy(() -> authService.login(request))
                    .isInstanceOf(BusinessException.class)
                    .hasMessageContaining(ErrorCode.USER_NOT_FOUND.getMessage());
        }

        @Test
        @DisplayName("비밀번호 불일치 시 INVALID_LOGIN 예외 발생")
        void login_invalidPassword() {
            // given
            AuthRequestDto.EmailLogin request =
                    new AuthRequestDto.EmailLogin("test@test.com", "wrongpw");

            UserEntity mockUser = UserEntity.create("test@test.com", "encodedPw", "tester");
            when(userRepository.findByEmail(request.getEmail())).thenReturn(Optional.of(mockUser));
            when(passwordEncoder.matches(request.getPassword(), mockUser.getPassword())).thenReturn(false);

            // when & then
            assertThatThrownBy(() -> authService.login(request))
                    .isInstanceOf(BusinessException.class)
                    .hasMessageContaining(ErrorCode.INVALID_LOGIN.getMessage());
        }
    }

    /**
     * 닉네임 중복 테스트
     */
    @Nested
    @DisplayName("닉네임 중복 확인 테스트")
    class CheckNickname {

        @Test
        @DisplayName("닉네임이 이미 존재하면 true 반환")
        void checkNickname_duplicate_true() {
            // given
            String nickname = "hello";
            when(userRepository.existsByNickname(nickname)).thenReturn(true);

            // when
            AuthResponseDto.NicknameDuplicate response = authService.checkNickname(nickname);

            // then
            assertThat(response.getIsDuplicate()).isTrue();
            verify(userRepository, times(1)).existsByNickname(nickname);
        }

        @Test
        @DisplayName("닉네임이 존재하지 않으면 false 반환")
        void checkNickname_duplicate_false() {
            // given
            String nickname = "newuser";
            when(userRepository.existsByNickname(nickname)).thenReturn(false);

            // when
            AuthResponseDto.NicknameDuplicate response = authService.checkNickname(nickname);

            // then
            assertThat(response.getIsDuplicate()).isFalse();
            verify(userRepository, times(1)).existsByNickname(nickname);
        }
    }
}