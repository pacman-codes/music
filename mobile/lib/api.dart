import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class Track {
  final int id;
  final String title, artist;
  final bool playable, liked;
  final int? rating;
  final String? decision;
  const Track(this.id,this.title,this.artist,this.playable,this.liked,this.rating,this.decision);
  factory Track.fromJson(Map<String,dynamic> j) => Track(
    j['id'] as int,j['title'] as String,j['artist'] as String,
    j['playable']==true,j['liked']==true,j['rating'] as int?,j['decision'] as String?);
  Track ready() => Track(id,title,artist,true,liked,rating,decision);
}

class ApiError implements Exception {
  final int status;
  const ApiError(this.status);
  @override
  String toString() => switch(status) {
    401 => 'Сессия истекла. Подключи устройство заново.',
    404 => 'Файл или запись недоступны.',
    0 => 'Сервер недоступен. Проверь подключение.',
    _ => 'Ошибка сервера ($status).',
  };
}

Uri serverUri(String raw) {
  final uri=Uri.tryParse(raw.trim());
  if(uri==null||uri.scheme!='https'||uri.host.isEmpty||uri.userInfo.isNotEmpty||
      uri.hasQuery||uri.hasFragment||(uri.path.isNotEmpty&&uri.path!='/')) {
    throw const FormatException('Укажи HTTPS-адрес сервера без пути.');
  }
  return uri.replace(path:'');
}

class LainApi {
  final Uri origin;
  final String token,deviceId;
  final http.Client client;
  LainApi(this.origin,this.token,this.deviceId,{http.Client? client}):client=client??http.Client();
  Uri uri(String path,[Map<String,String>? query]) => origin.replace(path:path,queryParameters:query);
  Map<String,String> get headers => {'Authorization':'Bearer $token'};
  Future<dynamic> request(String method,String path,{Map<String,String>? query,Object? body}) async {
    final req=http.Request(method,uri(path,query))..followRedirects=false;
    if(token.isNotEmpty)req.headers.addAll(headers);
    if(body!=null){req.headers['Content-Type']='application/json';req.body=jsonEncode(body);}
    try {
      final catalog=path.startsWith('/v1/discovery/')||path.startsWith('/v1/albums')||path.startsWith('/v1/artists')||path.startsWith('/v1/release-groups/');
      final response=await client.send(req).then(http.Response.fromStream).timeout(Duration(seconds:catalog?100:20));
      if(response.statusCode<200||response.statusCode>=300)throw ApiError(response.statusCode);
      return jsonDecode(response.body);
    } on TimeoutException {throw const ApiError(0);}
      on http.ClientException {throw const ApiError(0);}
  }
  static Future<LainApi> pair(String server,String code,String label) async {
    final temporary=LainApi(serverUri(server),'','');
    try {
      final data=await temporary.request('POST','/v1/pair',body:{'code':code.trim(),'label':label.trim()}) as Map;
      return LainApi(temporary.origin,data['token'] as String,data['device_id'] as String);
    } finally {temporary.client.close();}
  }
  List<Track> rows(dynamic data) => (data as List).map((j)=>Track.fromJson(Map<String,dynamic>.from(j as Map))).toList();
  Future<Map<String,dynamic>> library({String query='',int? before}) async =>
    Map<String,dynamic>.from(await request('GET','/v1/tracks',query:{'q':query,if(before!=null)'before':'$before'}) as Map);
  Future<Map<String,dynamic>> wave(String mode) async =>
    Map<String,dynamic>.from(await request('POST','/v1/wave/prepare',query:{'mode':mode}) as Map);
  Future<List<dynamic>> prefetch(List<int> ids,{bool status=false}) async {
    if(ids.isEmpty)return [];
    final data=await request('POST',status?'/v1/prefetch/status':'/v1/prefetch',body:{'track_ids':ids.take(5).toList()}) as Map;
    return data['results'] as List;
  }
  Future<void> report(Track track,String playId,String event,int listened,int? duration) async {
    await request('POST','/v1/playback',body:{'track_id':track.id,'play_id':playId,'event':event,
      'listened_ms':listened.clamp(0,86400000),'duration_ms':duration==null||duration<=0?null:duration.clamp(1,86400000)});
  }
  Future<void> rate(int id,int score) async {await request('PUT','/v1/tracks/$id/rating',body:{'score':score});}
  Future<void> react(int id,String status) async {await request('PUT','/v1/tracks/$id/reaction',body:{'status':status});}
  Future<List<dynamic>> playlists() async => await request('GET','/v1/playlists') as List;
  Future<Map<String,dynamic>> playlist(int id,int page) async =>
    Map<String,dynamic>.from(await request('GET','/v1/playlists/$id',query:{'page':'$page'}) as Map);
  Future<Map<String,dynamic>> settings() async => Map<String,dynamic>.from(await request('GET','/v1/wave/settings') as Map);
  Future<void> saveSettings(Map<String,dynamic> settings) async {await request('PUT','/v1/wave/settings',body:settings);}
  Future<List<dynamic>> history() async => (await request('GET','/v1/history') as Map)['results'] as List;
}

class Sessions {
  static const storage=FlutterSecureStorage();
  static Future<LainApi?> restore() async {
    final raw=await storage.read(key:'lain_session');
    if(raw==null)return null;
    final data=jsonDecode(raw) as Map;
    return LainApi(serverUri(data['origin'] as String),data['token'] as String,data['device'] as String);
  }
  static Future<void> save(LainApi api) async {
    await storage.write(key:'lain_session',value:jsonEncode({'origin':api.origin.toString(),'token':api.token,'device':api.deviceId}));
  }
  static Future<void> clear() => storage.delete(key:'lain_session');
}
